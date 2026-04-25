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


def test_find_project_root_honors_git_at_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dotfiles repo rooted at ~ is a real project — ``.git`` at HOME wins.

    The previous logic broke at HOME unconditionally, missing this case for
    any subdirectory session: config / policies / audit opt-out / KB lookup
    would all silently fall back to the literal session cwd.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".git").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from adjoint.paths import find_project_root

    nested = fake_home / "config" / "nvim"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == fake_home.resolve()


def test_find_project_root_ignores_global_dotdirs_at_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``~/.adjoint`` / ``~/.claude`` alone don't make HOME a project root —
    those exist globally after install. Only ``.git`` at HOME counts."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".adjoint").mkdir()
    (fake_home / ".claude").mkdir()
    # No .git at HOME → HOME is NOT a project root.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    from adjoint.paths import find_project_root

    nested = fake_home / "Downloads" / "tmp"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == nested.resolve()


def test_find_project_root_falls_back_to_start_when_no_marker(tmp_path: Path) -> None:
    from adjoint.paths import find_project_root

    plain = tmp_path / "plain"
    plain.mkdir()
    assert find_project_root(plain) == plain.resolve()
