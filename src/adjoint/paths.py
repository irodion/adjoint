"""Filesystem paths: ~/.adjoint layout and per-project scoping.

Set ``ADJOINT_HOME`` to override the user-home root (primarily for tests).
All paths are lazily computed; ``ensure()`` helpers create directories on demand.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


def _env_home() -> Path | None:
    raw = os.environ.get("ADJOINT_HOME")
    return Path(raw).expanduser() if raw else None


def adjoint_home() -> Path:
    return _env_home() or (Path.home() / ".adjoint")


def project_hash(project_path: Path | str) -> str:
    """Stable short identifier for a project directory.

    We use SHA-1 purely for its good-enough uniqueness in a directory-listing
    context — this is not a security boundary. ``usedforsecurity=False`` makes
    that intent explicit to linters and to readers.
    """
    abs_path = str(Path(project_path).expanduser().resolve())
    digest = hashlib.sha1(abs_path.encode("utf-8"), usedforsecurity=False).hexdigest()
    return digest[:12]


# Markers that identify a project root. ``.adjoint/`` is our own per-project
# config dir; ``.claude/`` is Claude Code's per-project settings dir (always
# present after ``adjoint install --project``); ``.git`` is the universal
# repo marker (file in worktrees / submodules, directory otherwise). The
# closest ancestor containing any of them wins; fall back to the start path
# if nothing matches.
_PROJECT_ROOT_MARKERS = (".adjoint", ".claude", ".git")


def find_project_root(start: Path | str) -> Path:
    """Walk up from ``start`` to find the enclosing project root.

    Claude Code launches hooks with ``cwd`` set to wherever the user started
    the session — which can be ``<repo>/subdir/`` rather than the repo root.
    Anything keyed on the project root (``load_config``, ``user_paths().project``,
    repo-boundary policies) needs the actual root, not the literal cwd.

    The walk-up stops at ``Path.home()`` exclusive: ``~/.adjoint/`` and
    ``~/.claude/`` exist globally after install, so without this guard a
    session started in any subdirectory of HOME would resolve to HOME itself
    as the "project". Sessions outside HOME (``/tmp``, ``/srv``) walk all the
    way up. Returns ``start`` when no marker is found below HOME.
    """
    here = Path(start).expanduser().resolve()
    try:
        home = Path.home().resolve()
    except RuntimeError:
        home = None
    for d in (here, *here.parents):
        if home is not None and d == home:
            # Don't auto-treat HOME as a project just because the global
            # ``~/.adjoint`` / ``~/.claude`` exist after install. But honor
            # an explicit ``.git`` at HOME — a dotfiles repo rooted at ~ is
            # a real project, and missing it would silently break config /
            # policies / audit / enrichment for any subdirectory session.
            if (d / ".git").exists():
                return d
            break
        if any((d / m).exists() for m in _PROJECT_ROOT_MARKERS):
            return d
    return here


@dataclass(frozen=True)
class UserPaths:
    root: Path

    @property
    def config_toml(self) -> Path:
        return self.root / "config.toml"

    @property
    def events_db(self) -> Path:
        return self.root / "events.db"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "adjoint.jsonl"

    @property
    def daemon_sock(self) -> Path:
        return self.root / "daemon.sock"

    @property
    def daemon_pid(self) -> Path:
        return self.root / "daemon.pid"

    @property
    def policies_enabled(self) -> Path:
        return self.root / "policies" / "enabled"

    @property
    def policies_disabled(self) -> Path:
        return self.root / "policies" / "disabled"

    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    @property
    def worktrees_dir(self) -> Path:
        return self.root / "worktrees"

    @property
    def examples_dir(self) -> Path:
        return self.root / "examples"

    def ensure(self) -> None:
        for d in (
            self.root,
            self.logs_dir,
            self.policies_enabled,
            self.policies_disabled,
            self.projects_dir,
            self.worktrees_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def project(self, project_path: Path | str) -> ProjectPaths:
        return ProjectPaths(user=self, project_path=Path(project_path).expanduser().resolve())


@dataclass(frozen=True)
class ProjectPaths:
    user: UserPaths
    project_path: Path

    @property
    def hash(self) -> str:
        return project_hash(self.project_path)

    @property
    def root(self) -> Path:
        return self.user.projects_dir / self.hash

    @property
    def project_toml(self) -> Path:
        return self.root / "project.toml"

    @property
    def daily_dir(self) -> Path:
        return self.root / "daily"

    @property
    def knowledge_dir(self) -> Path:
        return self.root / "knowledge"

    @property
    def knowledge_index(self) -> Path:
        return self.knowledge_dir / "index.md"

    @property
    def concepts_dir(self) -> Path:
        return self.knowledge_dir / "concepts"

    @property
    def connections_dir(self) -> Path:
        return self.knowledge_dir / "connections"

    @property
    def qa_dir(self) -> Path:
        return self.knowledge_dir / "qa"

    @property
    def state_json(self) -> Path:
        return self.root / "state.json"

    @property
    def worktrees_dir(self) -> Path:
        return self.user.worktrees_dir / self.hash

    def ensure(self) -> None:
        for d in (
            self.root,
            self.daily_dir,
            self.knowledge_dir,
            self.concepts_dir,
            self.connections_dir,
            self.qa_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def user_paths() -> UserPaths:
    return UserPaths(root=adjoint_home())


def bundled_dir() -> Path:
    """Location of bundled static assets packaged with adjoint."""
    return Path(__file__).parent / "bundled"


def migrations_dir() -> Path:
    return Path(__file__).parent / "store" / "migrations"


def claude_settings_path(scope: str, project_path: Path | str | None = None) -> Path:
    """Resolve the Claude Code settings.json to merge into.

    scope='user' → ~/.claude/settings.json
    scope='project' → <project>/.claude/settings.json
    """
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    if scope == "project":
        base = Path(project_path).expanduser().resolve() if project_path else Path.cwd()
        return base / ".claude" / "settings.json"
    raise ValueError(f"unknown scope: {scope!r}")
