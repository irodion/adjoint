from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
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


@pytest.fixture
def run_hook_bin(
    adjoint_home: Path,
) -> Callable[[str, str], subprocess.CompletedProcess]:
    """Invoke an installed ``adjoint-hook-*`` console script with stdin JSON.

    Locates the binary next to the active interpreter (setuptools entry points)
    and runs it with a minimal env that pins ``ADJOINT_HOME`` to the test home.
    """
    bin_dir = Path(sys.executable).parent

    def _run(binary: str, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(bin_dir / binary)],
            input=stdin,
            capture_output=True,
            text=True,
            env={"ADJOINT_HOME": str(adjoint_home), "PATH": "/usr/bin:/bin"},
            timeout=10,
        )

    return _run


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
