"""The 5-category extraction prompt used by ``flush``.

Philosophically aligned with the reference implementation
(coleam00/claude-memory-compiler) so the shape of daily logs remains
machine-readable in the same way.

Five required sections, ordered so that "why" context comes first and
"what's next" comes last:

1. ``## Context`` — 2–4 sentence narrative summary.
2. ``## Key Exchanges`` — quoted turns worth remembering.
3. ``## Decisions`` — decisions + rationale.
4. ``## Lessons Learned`` — patterns, gotchas, anti-patterns.
5. ``## Action Items`` — TODOs and follow-ups.
"""

from __future__ import annotations

from dataclasses import dataclass

SECTION_HEADINGS: tuple[str, ...] = (
    "Context",
    "Key Exchanges",
    "Decisions",
    "Lessons Learned",
    "Action Items",
)


SYSTEM_PROMPT = """\
You extract durable knowledge from a Claude Code session transcript.

Return Markdown with EXACTLY these five level-2 sections, in order, even if
empty (use "_none_" for empty sections):

## Context
## Key Exchanges
## Decisions
## Lessons Learned
## Action Items

Rules:
- Write tersely. No preamble, no meta commentary, no "in this session".
- Prefer concrete file paths, symbol names, and command names over generic nouns.
- In Key Exchanges, quote turns with > blockquotes; attribute with **user:** / **assistant:** prefixes.
- In Decisions, each bullet is `decision — rationale`.
- In Lessons Learned, each bullet is a generalizable pattern the reader can reuse.
- In Action Items, each bullet is imperative mood; mark blockers with `[blocker]`.
- Do NOT invent facts. If a section has no material, write `_none_` on its own line.
- Do NOT echo redaction tokens like `[REDACTED:...]` into quotes — summarise instead.

Output raw markdown only. No code fence around the whole thing.
"""


USER_PROMPT_TEMPLATE = """\
Session transcript (last {turns} turns, {chars} chars):

---
{transcript}
---

Extract a structured session log.
"""


@dataclass(frozen=True)
class ExtractionInput:
    transcript: str
    turns: int
    chars: int


def build_user_prompt(data: ExtractionInput) -> str:
    return USER_PROMPT_TEMPLATE.format(
        turns=data.turns,
        chars=data.chars,
        transcript=data.transcript,
    )


def section_pattern() -> str:
    """Regex fragment that matches any of our expected section headings."""
    return "|".join(SECTION_HEADINGS)


def frontmatter(
    *,
    session_id: str,
    reason: str,
    started_at: str,
    ended_at: str,
    turns: int,
    cost_usd: float | None,
) -> str:
    cost_str = f"{cost_usd:.4f}" if cost_usd is not None else "null"
    return (
        "---\n"
        f"session_id: {session_id}\n"
        f"reason: {reason}\n"
        f"started_at: {started_at}\n"
        f"ended_at: {ended_at}\n"
        f"turns: {turns}\n"
        f"cost_usd: {cost_str}\n"
        "---\n"
    )
