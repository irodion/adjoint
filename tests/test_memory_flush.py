"""Flush integration — transcript → redacted daily log with 5 sections."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from adjoint.config import load_config
from adjoint.memory.flush import flush, read_transcript, tail_slice

from .fake_agent import FakeAgent


def _write_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for role, text in turns:
        lines.append(
            json.dumps(
                {
                    "type": role,
                    "timestamp": "2026-04-24T12:00:00Z",
                    "message": {
                        "role": role,
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_tail_slice_honours_both_caps() -> None:
    turns = [
        type("T", (), {"role": "user", "text": "x" * 5000, "timestamp": None})() for _ in range(10)
    ]
    # char cap hits first (15000 / ~5000 per turn = 3)
    selected = tail_slice(turns, max_turns=100, max_chars=15_000)  # type: ignore[arg-type]
    assert 2 <= len(selected) <= 3

    # turn cap hits first
    selected2 = tail_slice(turns, max_turns=2, max_chars=1_000_000)  # type: ignore[arg-type]
    assert len(selected2) == 2


def test_flush_writes_daily_log_with_frontmatter_and_redacts(
    adjoint_home: Path, project_dir: Path
) -> None:
    transcript = project_dir / ".claude" / "transcripts" / "session-abc.jsonl"
    _write_transcript(
        transcript,
        [
            ("user", "here is my key sk-ant-SUPERSECRET123ABCDEF and some context"),
            ("assistant", "I'll note the key in memory for future use"),
            ("user", "what did we decide?"),
            ("assistant", "We decided to use Haiku for cost reasons."),
        ],
    )

    extraction = (
        "## Context\nWe discussed API key handling and model choice.\n\n"
        "## Key Exchanges\n> user: sk-ant-SUPERSECRET123ABCDEF\n> assistant: noted\n\n"
        "## Decisions\n- use Haiku — cheapest for extraction\n\n"
        "## Lessons Learned\n- redact keys on the way in AND out\n\n"
        "## Action Items\n- [blocker] add test coverage\n"
    )
    agent = FakeAgent().enqueue(extraction)

    result = flush(
        transcript_path=transcript,
        project_path=project_dir,
        session_id="sess-1",
        reason="session_end",
        client=agent,
        now_utc=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
    )

    assert result.turns == 4
    assert result.daily_log.is_file()
    content = result.daily_log.read_text(encoding="utf-8")
    # Frontmatter.
    assert content.startswith("---\nsession_id: sess-1\n")
    assert "reason: session_end" in content
    assert "turns: 4" in content
    # All 5 sections present.
    for heading in (
        "## Context",
        "## Key Exchanges",
        "## Decisions",
        "## Lessons Learned",
        "## Action Items",
    ):
        assert heading in content
    # Redaction — the raw key must never land in the daily log, but the
    # redaction tag may appear via the post-LLM pass if the LLM echoed it.
    assert "sk-ant-SUPERSECRET" not in content

    # Agent was called exactly once, with no tools, model set, and recursion tag.
    assert len(agent.calls) == 1
    assert agent.calls[0]["allowed_tools"] == []
    assert agent.calls[0]["recursion_tag"] == "adjoint_flush"


def test_flush_on_empty_transcript_is_noop(adjoint_home: Path, project_dir: Path) -> None:
    transcript = project_dir / ".claude" / "empty.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("", encoding="utf-8")

    agent = FakeAgent()
    result = flush(
        transcript_path=transcript,
        project_path=project_dir,
        client=agent,
    )
    assert result.turns == 0
    assert result.bytes_appended == 0
    # Never called the LLM on an empty transcript.
    assert agent.calls == []


def test_read_transcript_renders_tool_uses(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "let me check"},
                        {"type": "tool_use", "name": "Read", "input": {}},
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    turns = read_transcript(p)
    assert len(turns) == 1
    assert "let me check" in turns[0].text
    assert "[tool: Read]" in turns[0].text


def test_flush_respects_config_defaults_exist(adjoint_home: Path, project_dir: Path) -> None:
    """Config loads without error for a fresh project (covers the happy-path)."""
    cfg = load_config(project_dir)
    assert cfg.memory.flush_on_session_end is True
    assert cfg.providers["claude"].model  # a default model is present
