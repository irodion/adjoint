from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def adjoint_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "adjoint-home"
    monkeypatch.setenv("ADJOINT_HOME", str(home))
    # Reset logging module config so a new handler points at tmp_path.
    from adjoint import log as adjoint_log

    monkeypatch.setattr(adjoint_log, "_configured", False)
    yield home


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fake-project"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _no_recursion_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests should never inherit an adjoint recursion marker from the parent env.
    monkeypatch.delenv("CLAUDE_INVOKED_BY", raising=False)
    assert os.environ.get("CLAUDE_INVOKED_BY") is None


def write_article(
    path: Path,
    *,
    title: str,
    tags: list[str],
    created: str,
    updated: str,
    body: str,
    kind: str = "concept",
) -> None:
    """Write a minimal valid KB article — shared by memory test suites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tag_yaml = "[" + ", ".join(tags) + "]"
    path.write_text(
        f"---\ntitle: {title}\nkind: {kind}\ntags: {tag_yaml}\n"
        f"created: {created}\nupdated: {updated}\n"
        f"sources:\n  - daily/2026-04-24.md\ncost_usd: 0.01\n---\n\n"
        f"# {title}\n\n{body}\n",
        encoding="utf-8",
    )
