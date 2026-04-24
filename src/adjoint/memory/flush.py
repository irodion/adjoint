"""Flush — distil a single Claude Code session transcript into today's daily log.

Invoked either:

* as a detached subprocess from the ``SessionEnd`` / ``PreCompact`` hook, or
* manually via ``adjoint memory flush --transcript <path>``.

Never runs inline in the hook — the whole point is that the user's IDE exits
immediately while flush continues in the background.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..config import Config, load_config
from ..log import get_logger, log_event
from ..paths import UserPaths, user_paths
from ..store.files import append_text, daily_log_path
from .agent import AgentClient, AgentRequest, complete_sync, default_client
from .extractor import SYSTEM_PROMPT, ExtractionInput, build_user_prompt, frontmatter
from .redact import from_config as redactor_from_config

FlushReason = Literal["session_end", "precompact", "manual"]

MAX_TURNS_DEFAULT = 30
MAX_CHARS_DEFAULT = 15_000


@dataclass(frozen=True)
class TranscriptTurn:
    role: str  # "user" | "assistant"
    text: str  # extracted text; tool uses rendered as [tool: Name]
    timestamp: str | None = None


def _message_to_text(msg: dict) -> str:
    """Render a Claude Code transcript entry as a short text block."""
    message = msg.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text") or "")
            elif btype == "tool_use":
                parts.append(f"[tool: {block.get('name', '?')}]")
            elif btype == "tool_result":
                raw = block.get("content")
                if isinstance(raw, list):
                    inner = " ".join(b.get("text", "") for b in raw if isinstance(b, dict))
                else:
                    inner = str(raw or "")
                parts.append(f"[tool_result: {inner[:200]}]")
            elif btype == "thinking":
                # Skip thinking blocks — they're noise for extraction.
                continue
    return "\n".join(p for p in parts if p).strip()


def read_transcript(path: Path) -> list[TranscriptTurn]:
    """Parse a Claude Code transcript JSONL into a flat list of turns.

    Only user and assistant messages are returned. Tool calls are rendered
    inline as ``[tool: Name]`` so they keep a footprint but don't dominate.
    """
    if not path.is_file():
        return []
    turns: list[TranscriptTurn] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg_type = obj.get("type")
        if msg_type not in ("user", "assistant"):
            continue
        text = _message_to_text(obj)
        if not text:
            continue
        turns.append(
            TranscriptTurn(
                role=msg_type,
                text=text,
                timestamp=obj.get("timestamp"),
            )
        )
    return turns


def tail_slice(
    turns: list[TranscriptTurn],
    *,
    max_turns: int = MAX_TURNS_DEFAULT,
    max_chars: int = MAX_CHARS_DEFAULT,
) -> list[TranscriptTurn]:
    """Take the tail, capped by whichever bound hits first."""
    selected: list[TranscriptTurn] = []
    char_count = 0
    for t in reversed(turns):
        candidate_chars = char_count + len(t.text) + len(t.role) + 2
        if len(selected) >= max_turns:
            break
        if candidate_chars > max_chars and selected:
            break
        selected.append(t)
        char_count = candidate_chars
    return list(reversed(selected))


def render_turns(turns: list[TranscriptTurn]) -> str:
    return "\n\n".join(f"**{t.role}:** {t.text}" for t in turns)


@dataclass(frozen=True)
class FlushResult:
    daily_log: Path
    turns: int
    cost_usd: float | None
    bytes_appended: int


def flush(
    *,
    transcript_path: Path,
    project_path: Path,
    session_id: str | None = None,
    reason: FlushReason = "session_end",
    config: Config | None = None,
    client: AgentClient | None = None,
    paths: UserPaths | None = None,
    now_utc: datetime | None = None,
) -> FlushResult:
    """Distil a transcript and append the result to today's daily log.

    Returns ``FlushResult`` with the path written, number of turns extracted,
    LLM cost, and bytes appended. Safe to call multiple times — each call
    appends a new section; the reader can distinguish them by their
    frontmatter.
    """
    cfg = config or load_config(project_path)
    agent = client or default_client()
    up = paths or user_paths()
    pp = up.project(project_path)
    pp.ensure()
    logger = get_logger("memory.flush")

    turns = read_transcript(transcript_path)
    selected = tail_slice(turns)
    if not selected:
        log_event(
            logger,
            "flush.skip.empty_transcript",
            transcript=str(transcript_path),
            project=str(project_path),
        )
        return FlushResult(
            daily_log=daily_log_path(pp.daily_dir),
            turns=0,
            cost_usd=None,
            bytes_appended=0,
        )

    redactor = redactor_from_config(cfg.memory.redact_patterns)
    transcript_text = render_turns(selected)
    transcript_text = redactor.sanitize(transcript_text)

    extraction = ExtractionInput(
        transcript=transcript_text,
        turns=len(selected),
        chars=len(transcript_text),
    )
    req = AgentRequest(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(extraction),
        model=cfg.model_for("claude"),
        allowed_tools=[],
        max_turns=1,
        recursion_tag="adjoint_flush",
    )
    response = complete_sync(agent, req)

    body = redactor.sanitize(response.text.strip())
    if not body:
        log_event(logger, "flush.skip.empty_response", turns=len(selected))
        return FlushResult(
            daily_log=daily_log_path(pp.daily_dir),
            turns=len(selected),
            cost_usd=response.cost_usd,
            bytes_appended=0,
        )

    now = now_utc or datetime.now(UTC)
    started_at = selected[0].timestamp or now.isoformat(timespec="seconds")
    ended_at = selected[-1].timestamp or now.isoformat(timespec="seconds")
    fm = frontmatter(
        session_id=session_id or transcript_path.stem,
        reason=reason,
        started_at=started_at,
        ended_at=ended_at,
        turns=len(selected),
        cost_usd=response.cost_usd,
    )
    chunk = fm + "\n" + body + "\n\n"

    out_path = daily_log_path(pp.daily_dir, now.strftime("%Y-%m-%d"))
    append_text(out_path, chunk)
    log_event(
        logger,
        "flush.ok",
        daily_log=str(out_path),
        turns=len(selected),
        cost_usd=response.cost_usd,
        duration_ms=response.duration_ms,
        reason=reason,
    )
    return FlushResult(
        daily_log=out_path,
        turns=len(selected),
        cost_usd=response.cost_usd,
        bytes_appended=len(chunk),
    )
