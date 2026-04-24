"""Session-start injection payload — structure + byte cap."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from adjoint.memory.index import render_index

from .conftest import write_article


def _write_article(path: Path, *, title: str, tags: list[str], updated: str, body: str) -> None:
    write_article(path, title=title, tags=tags, created=updated, updated=updated, body=body)


def test_render_index_groups_by_tag_and_respects_recency(
    adjoint_home: Path, project_dir: Path
) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    _write_article(
        pp.concepts_dir / "caching.md",
        title="Prompt Caching",
        tags=["perf", "anthropic"],
        updated="2026-04-24",
        body="Short summary paragraph about prompt caching for performance.",
    )
    _write_article(
        pp.concepts_dir / "context.md",
        title="Context Window",
        tags=["anthropic"],
        updated="2026-01-01",  # older, outside recent window
        body="Paragraph about context windows.",
    )

    out = render_index(pp, now=date(2026, 4, 24))
    assert "# Knowledge Index" in out
    assert "## Recently Updated" in out
    assert "[[concepts/caching.md|Prompt Caching]]" in out
    # Older article still appears in By Tag even if not "recent".
    assert "## By Tag" in out
    assert "Context Window" in out


def test_render_index_surfaces_action_items_from_recent_dailies(
    adjoint_home: Path, project_dir: Path
) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    (pp.daily_dir / "2026-04-24.md").write_text(
        "---\nsession_id: s1\n---\n\n## Action Items\n- [blocker] ship M1\n- write lint tests\n",
        encoding="utf-8",
    )
    out = render_index(pp, now=date(2026, 4, 24))
    assert "## Active Action Items" in out
    assert "[blocker] ship M1" in out
    assert "write lint tests" in out


def test_render_index_respects_byte_cap(adjoint_home: Path, project_dir: Path) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    for i in range(20):
        _write_article(
            pp.concepts_dir / f"article-{i}.md",
            title=f"Article {i}",
            tags=[f"tag{i}"],
            updated="2026-04-24",
            body=("lorem ipsum " * 200),
        )
    out = render_index(pp, max_bytes=2048, now=date(2026, 4, 24))
    assert len(out.encode("utf-8")) <= 2048
    # Header always present.
    assert "# Knowledge Index" in out
