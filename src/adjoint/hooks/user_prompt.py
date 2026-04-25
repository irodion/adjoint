"""UserPromptSubmit hook — inject [[wikilinks]] into user prompts (opt-in).

Gated on ``memory.enrich_prompts`` (default False). Tokenises the prompt and
each concept's slug + frontmatter title, scores by token-set intersection
(slug overlap weighted 2x since the filename encodes the canonical topic),
and emits up to three ``[[concepts/<slug>]]`` references as
``additionalContext``. Intentionally no LLM call - cheap heuristic only.

Any error or budget miss collapses to pass-through (return None).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from ..config import load_config
from ..memory._shared import parse_frontmatter
from ..paths import find_project_root, user_paths
from ._runtime import HookInput, run_hook

_STOPWORDS = frozenset(
    {
        # 2-3 char common English words. Required because ``_TOKEN_RE`` now
        # accepts 2+ char tokens - otherwise "the" / "and" / "for" would
        # leak into prompt and slug token sets and create noise.
        "an",
        "as",
        "at",
        "be",
        "by",
        "do",
        "go",
        "he",
        "if",
        "in",
        "is",
        "it",
        "me",
        "my",
        "no",
        "of",
        "on",
        "or",
        "so",
        "to",
        "up",
        "us",
        "we",
        "the",
        "and",
        "for",
        "are",
        "you",
        "but",
        "not",
        "all",
        "can",
        "had",
        "her",
        "his",
        "its",
        "our",
        "out",
        "she",
        "him",
        "any",
        "one",
        "two",
        "now",
        "way",
        "how",
        "why",
        "who",
        "did",
        "get",
        "got",
        "let",
        "may",
        "set",
        "too",
        "say",
        "see",
        "yes",
        "off",
        "old",
        "new",
        "yet",
        "own",
        "ago",
        "few",
        "via",
        "etc",
        # 4+ char (kept as-is). Tools-of-the-trade like ``MCP`` / ``WAL`` /
        # ``CLI`` / ``uv`` are deliberately *not* here so they can still
        # match concept slugs and titles.
        "this",
        "that",
        "these",
        "those",
        "from",
        "with",
        "about",
        "what",
        "when",
        "where",
        "which",
        "while",
        "would",
        "could",
        "should",
        "there",
        "their",
        "them",
        "then",
        "than",
        "have",
        "been",
        "does",
        "doing",
        "going",
        "into",
        "upon",
        "over",
        "under",
        "just",
        "some",
        "such",
        "will",
        "make",
        "made",
        "only",
        "also",
        "your",
        "yours",
        "mine",
        "much",
        "many",
    }
)
# 2+ chars so short technical acronyms — MCP, CLI, SDK, WAL, uv — match.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
_MIN_TOKEN_LEN = 2
_MAX_MATCHES = 3

# Bytes read from each concept file when extracting frontmatter. The full
# file body is irrelevant for the title-overlap heuristic — frontmatter is
# always at the top, and 2 KB comfortably fits even articles with long tag
# lists or many sources. A 50 KB concept article therefore costs ~2 KB of
# I/O per prompt instead of a full read.
_FRONTMATTER_READ_BYTES = 2048


def _tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text) if tok.lower() not in _STOPWORDS}


def _find_related_concepts(prompt: str, concepts_dir: Path, limit: int) -> list[str]:
    prompt_toks = _tokens(prompt)
    if not prompt_toks:
        return []
    scored: list[tuple[int, str]] = []
    for md in concepts_dir.glob("*.md"):
        slug = md.stem
        slug_toks = {p for p in re.split(r"[-_]", slug.lower()) if len(p) >= _MIN_TOKEN_LEN}
        try:
            with md.open("rb") as f:
                head = f.read(_FRONTMATTER_READ_BYTES)
        except OSError:
            continue
        text = head.decode("utf-8", errors="replace")
        fm, _ = parse_frontmatter(text)
        title_toks = _tokens(fm.get("title", ""))
        # Slug hits count double — filename encodes the canonical topic.
        score = len(prompt_toks & slug_toks) * 2 + len(prompt_toks & title_toks)
        if score > 0:
            scored.append((score, slug))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [slug for _, slug in scored[:limit]]


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    cwd = hook_input.cwd
    if not cwd:
        return None
    # Project KB lives at the repo root, regardless of where Claude was
    # launched. ``project_hash`` of <repo>/subdir would key a different,
    # empty project directory — losing all prompt enrichment for nested
    # sessions.
    project_root = find_project_root(cwd)
    cfg = load_config(project_root)
    if not cfg.memory.enrich_prompts:
        return None

    prompt = str(hook_input.raw.get("prompt", "")).strip()
    if not prompt:
        return None

    pp = user_paths().project(project_root)
    if not pp.concepts_dir.is_dir():
        return None

    matches = _find_related_concepts(prompt, pp.concepts_dir, limit=_MAX_MATCHES)
    if not matches:
        return None

    links = " ".join(f"[[concepts/{slug}]]" for slug in matches)
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"Related: {links}",
        }
    }


def main() -> int:
    return run_hook("user_prompt", handle, timeout_s=1.0, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
