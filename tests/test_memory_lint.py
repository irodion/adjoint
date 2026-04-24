"""Deterministic lint checks 1–5 on a synthetic knowledge base."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from adjoint.memory.lint import lint

from .conftest import write_article as _article


def test_lint_detects_broken_wikilink(adjoint_home: Path, project_dir: Path) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    _article(
        pp.concepts_dir / "foo.md",
        title="Foo",
        tags=["x"],
        created="2026-01-01",
        updated="2026-04-24",
        body="Foo references [[nonexistent-slug]] which does not exist. " * 30,
    )

    report = lint(project_path=project_dir, cheap=True, now=date(2026, 4, 24))
    assert any(
        i.check == "broken_wikilink" and "nonexistent-slug" in i.message for i in report.issues
    )


def test_lint_detects_orphan_after_7_days(adjoint_home: Path, project_dir: Path) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    _article(
        pp.concepts_dir / "lonely.md",
        title="Lonely",
        tags=["x"],
        created="2026-01-01",
        updated="2026-04-01",
        body="A very alone article. " * 30,
    )
    _article(
        pp.concepts_dir / "new.md",
        title="New",
        tags=["x"],
        created="2026-04-23",  # within 7 days
        updated="2026-04-23",
        body="A young article, should not be flagged. " * 30,
    )

    report = lint(project_path=project_dir, cheap=True, now=date(2026, 4, 24))
    checks = {(i.check, i.article) for i in report.issues}
    assert ("orphan", "concepts/lonely.md") in checks
    assert ("orphan", "concepts/new.md") not in checks


def test_lint_detects_sparse(adjoint_home: Path, project_dir: Path) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    _article(
        pp.concepts_dir / "thin.md",
        title="Thin",
        tags=["x"],
        created="2026-04-24",
        updated="2026-04-24",
        body="tiny body",
    )
    report = lint(project_path=project_dir, cheap=True, now=date(2026, 4, 24))
    assert any(i.check == "sparse" and i.article == "concepts/thin.md" for i in report.issues)


def test_lint_detects_missing_backlinks(adjoint_home: Path, project_dir: Path) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    # a.md links to b.md; b.md has no Backlinks section → missing backlink.
    _article(
        pp.concepts_dir / "a.md",
        title="A",
        tags=["x"],
        created="2026-04-24",
        updated="2026-04-24",
        body="Link to [[b]]. " * 30,
    )
    _article(
        pp.concepts_dir / "b.md",
        title="B",
        tags=["x"],
        created="2026-04-24",
        updated="2026-04-24",
        body="B content. " * 30,
    )
    report = lint(project_path=project_dir, cheap=True, now=date(2026, 4, 24))
    assert any(i.check == "missing_backlink" and i.article == "b.md" for i in report.issues)


def test_lint_writes_report(adjoint_home: Path, project_dir: Path) -> None:
    from adjoint.paths import user_paths

    pp = user_paths().project(project_dir)
    pp.ensure()
    _article(
        pp.concepts_dir / "ok.md",
        title="OK",
        tags=["x"],
        created="2026-04-24",
        updated="2026-04-24",
        body="body " * 200,
    )
    lint(project_path=project_dir, cheap=True, now=date(2026, 4, 24))
    report_path = pp.knowledge_dir / ".lint-report.md"
    assert report_path.is_file()
    text = report_path.read_text(encoding="utf-8")
    assert "Knowledge Base — Lint Report" in text
    for check in ("broken_wikilink", "orphan", "stale", "sparse", "missing_backlink"):
        assert f"## {check}" in text
