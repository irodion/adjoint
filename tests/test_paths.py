"""Tests for path helpers — primarily ``find_project_root``."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_find_project_root_finds_marker(tmp_path: Path) -> None:
    from adjoint.paths import find_project_root

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "sub" / "deep"
    nested.mkdir(parents=True)

    assert find_project_root(nested) == repo.resolve()


def test_find_project_root_skips_home_dotdirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``~/.adjoint`` and ``~/.claude`` exist after install; HOME itself must
    NOT be treated as a project root for sessions started in ``~/Downloads/x``.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".adjoint").mkdir()
    (fake_home / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from adjoint.paths import find_project_root

    session = fake_home / "Downloads" / "tmp"
    session.mkdir(parents=True)
    # The walk-up must stop at HOME and fall back to the start path,
    # *not* return HOME just because it has marker dirs.
    resolved = find_project_root(session)
    assert resolved == session.resolve()
    assert resolved != fake_home.resolve()


def test_find_project_root_finds_marker_below_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real repo under HOME still resolves correctly — only HOME itself
    is excluded as a candidate root."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".adjoint").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from adjoint.paths import find_project_root

    repo = fake_home / "Projects" / "adjoint"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src" / "adjoint"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == repo.resolve()


def test_find_project_root_falls_back_to_start_when_no_marker(tmp_path: Path) -> None:
    from adjoint.paths import find_project_root

    plain = tmp_path / "plain"
    plain.mkdir()
    assert find_project_root(plain) == plain.resolve()
