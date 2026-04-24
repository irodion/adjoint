"""UserPromptSubmit hook — inject [[wikilinks]] into user prompts (opt-in).

Gated on ``memory.enrich_prompts`` (default False). Tokenises the prompt,
scans ``knowledge/concepts/*.md`` for slug-token or title substring overlap,
and emits up to three ``[[concepts/<slug>]]`` references as
``additionalContext``. Intentionally no LLM call — cheap heuristic only.

Any error or budget miss collapses to pass-through (return None).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from ..config import load_config
from ..memory._shared import parse_frontmatter
from ..paths import user_paths
from ._runtime import HookInput, run_hook

_STOPWORDS = frozenset(
    {
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
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")
_MAX_MATCHES = 3


def _tokens(text: str) -> set[str]:
    return {tok.lower() for tok in _TOKEN_RE.findall(text) if tok.lower() not in _STOPWORDS}


def _find_related_concepts(prompt: str, concepts_dir: Path, limit: int) -> list[str]:
    prompt_toks = _tokens(prompt)
    if not prompt_toks:
        return []
    scored: list[tuple[int, str]] = []
    for md in concepts_dir.glob("*.md"):
        slug = md.stem
        slug_toks = {p for p in re.split(r"[-_]", slug.lower()) if len(p) >= 4}
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
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
    cfg = load_config(cwd)
    if not cfg.memory.enrich_prompts:
        return None

    prompt = str(hook_input.raw.get("prompt", "")).strip()
    if not prompt:
        return None

    pp = user_paths().project(cwd)
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
