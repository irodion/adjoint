"""Session-start injection payload — ``knowledge/index.md``.

Shape: a compact markdown digest injected as ``additionalContext`` by the
SessionStart hook so every new Claude Code session begins with the project's
accumulated memory. Hard 20 KB cap.

Layout (top-down, by priority):

1. ``## Active Action Items`` — unchecked TODOs pulled from the most recent
   daily logs. Most useful thing to see first.
2. ``## Recently Updated`` — articles updated in the last 14 days.
3. ``## By Tag`` — per-tag groups, each with most-recently-updated articles
   first. Tags ordered by most-recent article within the tag.

When the full document exceeds the byte cap we truncate **tail-first** —
oldest tag groups are dropped before recent ones. Active action items and
"Recently Updated" are never dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ..paths import ProjectPaths
from ._shared import first_paragraph, parse_frontmatter

DEFAULT_MAX_BYTES = 20 * 1024
_RECENT_WINDOW_DAYS = 14
_ACTION_ITEMS_DAYS = 14
_PREVIEW_CHARS = 180


@dataclass
class ArticleMeta:
    path: Path
    rel_path: str
    title: str
    tags: list[str] = field(default_factory=list)
    updated: str = ""
    preview: str = ""


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        inner = raw.strip("[]")
        return [t.strip().strip("'\"") for t in inner.split(",") if t.strip()]
    return [t.strip() for t in raw.split(",") if t.strip()]


def _load_article(path: Path, base: Path) -> ArticleMeta | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, body = parse_frontmatter(text)
    title = fm.get("title") or path.stem
    tags = _parse_tags(fm.get("tags", ""))
    updated = fm.get("updated") or ""
    preview = first_paragraph(body)[:_PREVIEW_CHARS].strip()
    rel = str(path.relative_to(base))
    return ArticleMeta(
        path=path,
        rel_path=rel,
        title=title,
        tags=tags,
        updated=updated,
        preview=preview,
    )


def _collect_articles(project_paths: ProjectPaths) -> list[ArticleMeta]:
    base = project_paths.knowledge_dir
    if not base.is_dir():
        return []
    out: list[ArticleMeta] = []
    for sub in (project_paths.concepts_dir, project_paths.connections_dir, project_paths.qa_dir):
        if not sub.is_dir():
            continue
        for p in sub.glob("*.md"):
            meta = _load_article(p, base)
            if meta:
                out.append(meta)
    return out


def _is_recent(updated: str, *, within_days: int, now: date) -> bool:
    if not updated:
        return False
    try:
        dt = datetime.fromisoformat(updated).date()
    except ValueError:
        try:
            dt = date.fromisoformat(updated[:10])
        except ValueError:
            return False
    return (now - dt) <= timedelta(days=within_days)


def _gather_action_items(project_paths: ProjectPaths, *, within_days: int, now: date) -> list[str]:
    """Pull unchecked ``## Action Items`` bullets from recent daily logs."""
    if not project_paths.daily_dir.is_dir():
        return []
    cutoff = now - timedelta(days=within_days)
    items: list[str] = []
    seen: set[str] = set()
    for p in sorted(project_paths.daily_dir.glob("*.md"), reverse=True):
        try:
            log_date = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if log_date < cutoff:
            break
        for section_body in _iter_sections(p.read_text(encoding="utf-8"), "Action Items"):
            for line in section_body.splitlines():
                stripped = line.strip()
                if stripped.startswith(("- ", "* ")) and stripped not in seen:
                    if "_none_" in stripped.lower():
                        continue
                    items.append(stripped)
                    seen.add(stripped)
    return items


def _iter_sections(text: str, heading: str):
    """Yield bodies of every ``## {heading}`` section in the doc."""
    pat = re.compile(rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
    for m in pat.finditer(text):
        yield m.group(1)


def _tag_groups(articles: list[ArticleMeta]) -> list[tuple[str, list[ArticleMeta]]]:
    """Group articles by primary tag, sorted by most-recent-update within group."""
    by_tag: dict[str, list[ArticleMeta]] = {}
    for a in articles:
        primary = a.tags[0] if a.tags else "untagged"
        by_tag.setdefault(primary, []).append(a)
    for xs in by_tag.values():
        xs.sort(key=lambda m: m.updated, reverse=True)
    # Tags ordered by most recent article (tag with freshest content first).
    return sorted(
        by_tag.items(),
        key=lambda kv: kv[1][0].updated if kv[1] else "",
        reverse=True,
    )


def _render_article_line(a: ArticleMeta) -> str:
    if a.preview:
        return f"- [[{a.rel_path}|{a.title}]] — {a.preview}"
    return f"- [[{a.rel_path}|{a.title}]]"


def render_index(
    project_paths: ProjectPaths,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    now: date | None = None,
) -> str:
    """Render the session-start payload, honouring a hard ``max_bytes`` cap.

    Drop order when over budget: By Tag groups (oldest first) → older Recently
    Updated entries → older Action Items. The header is always included.
    """
    now_d = now or datetime.now(UTC).date()
    articles = _collect_articles(project_paths)

    action_items = _gather_action_items(project_paths, within_days=_ACTION_ITEMS_DAYS, now=now_d)
    recent = [
        a for a in articles if _is_recent(a.updated, within_days=_RECENT_WINDOW_DAYS, now=now_d)
    ]
    recent.sort(key=lambda a: a.updated, reverse=True)
    groups = _tag_groups(articles)

    header = "# Knowledge Index\n\n"
    out = [header]
    budget = max_bytes - len(header.encode("utf-8"))

    def _fits(chunk: str) -> bool:
        nonlocal budget
        size = len(chunk.encode("utf-8"))
        if size > budget:
            return False
        budget -= size
        return True

    # Priority 1 — Active Action Items.
    if action_items and _fits("## Active Action Items\n"):
        out.append("## Active Action Items\n")
        for item in action_items:
            line = item + "\n"
            if not _fits(line):
                break
            out.append(line)
        if _fits("\n"):
            out.append("\n")

    # Priority 2 — Recently Updated.
    if recent and _fits("## Recently Updated\n"):
        out.append("## Recently Updated\n")
        for a in recent:
            line = _render_article_line(a) + "\n"
            if not _fits(line):
                break
            out.append(line)
        if _fits("\n"):
            out.append("\n")

    # Priority 3 — By Tag (whole groups only; partial groups drop cleanly).
    if groups and _fits("## By Tag\n"):
        out.append("## By Tag\n")
        for tag, group in groups:
            chunk_lines = [f"\n### {tag}\n"] + [_render_article_line(a) + "\n" for a in group]
            chunk_lines.append("\n")
            chunk = "".join(chunk_lines)
            if _fits(chunk):
                out.append(chunk)

    return "".join(out)


def write_index(
    project_paths: ProjectPaths,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    text = render_index(project_paths, max_bytes=max_bytes)
    path = project_paths.knowledge_index
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
