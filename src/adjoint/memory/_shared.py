"""Shared primitives used by ``flush``, ``compile``, ``index``, and ``lint``.

Three modules independently reached for the same frontmatter parser, wikilink
regex, backlink-stripper, and first-paragraph extractor. Keeping them co-located
means the KB's on-disk shape has exactly one Python representation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

Kind = Literal["concept", "connection", "qa"]
KINDS: tuple[Kind, ...] = ("concept", "connection", "qa")

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_BACKLINKS_RE = re.compile(r"\n##\s+Backlinks\s*\n.*$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split ``---``-delimited YAML frontmatter from a markdown body.

    Returns ``({}, text)`` if no frontmatter is present. Only scalar values
    are surfaced (list-valued keys like ``sources:`` map to an empty string —
    call sites that need them parse the raw list from the original text).
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block, body = m.group(1), m.group(2)
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip()
    return fm, body


def strip_backlinks(body: str) -> str:
    """Drop any trailing ``## Backlinks`` section — regenerated deterministically."""
    return _BACKLINKS_RE.sub("", body.strip()).strip()


def wikilink_targets(text: str) -> set[str]:
    """Return the set of slugs referenced by ``[[wikilinks]]`` in ``text``.

    A wikilink target may be a bare slug (``[[foo]]``) or a relative path
    (``[[concepts/foo.md|Alias]]``). Both normalise to the slug ``foo``.
    """
    out: set[str] = set()
    for m in WIKILINK_RE.finditer(text):
        slug = Path(m.group(1).strip()).stem
        if slug:
            out.add(slug)
    return out


def first_paragraph(body: str) -> str:
    """First non-heading, non-empty paragraph, with internal whitespace collapsed."""
    for chunk in body.split("\n\n"):
        stripped = chunk.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return " ".join(stripped.split())
    return ""
