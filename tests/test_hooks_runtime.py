"""Hook runtime contract tests — recursion guard and fail-open semantics."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK_BINS = [
    "adjoint-hook-session-start",
    "adjoint-hook-session-end",
    "adjoint-hook-pre-compact",
    "adjoint-hook-pre-tool-use",
    "adjoint-hook-post-tool-use",
    "adjoint-hook-user-prompt",
]


def _bin_path(name: str) -> str:
    # Installed console scripts live alongside the Python interpreter.
    return str(Path(sys.executable).parent / name)


def _disable_flush(project_dir: Path) -> None:
    """Write a project-level config that turns off background flush spawns.

    We deliberately do NOT use the recursion-guard env var here, because that
    short-circuits the hook before ``read_stdin_json`` or the handler runs and
    would reduce these tests to a recursion-guard-only check. Disabling the
    flush triggers at the config layer lets the real handler path execute
    without leaking ``adjoint memory flush`` subprocesses.
    """
    cfg_dir = project_dir / ".adjoint"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.toml").write_text(
        "[memory]\nflush_on_session_end = false\nflush_on_precompact = false\n",
        encoding="utf-8",
    )


def test_every_hook_exits_0_on_valid_json(adjoint_home: Path, project_dir: Path) -> None:
    _disable_flush(project_dir)
    stdin_payload = json.dumps(
        {
            "session_id": "test-session",
            "transcript_path": str(project_dir / "nonexistent.jsonl"),
            "cwd": str(project_dir),
            "hook_event_name": "Test",
        }
    )
    env = {"ADJOINT_HOME": str(adjoint_home), "PATH": "/usr/bin:/bin"}
    for name in HOOK_BINS:
        cp = subprocess.run(
            [_bin_path(name)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert cp.returncode == 0, f"{name} exited {cp.returncode}: {cp.stderr}"


def test_hook_short_circuits_on_recursion_marker(adjoint_home: Path) -> None:
    env = {
        "ADJOINT_HOME": str(adjoint_home),
        "PATH": "/usr/bin:/bin",
        "CLAUDE_INVOKED_BY": "adjoint_flush",
    }
    cp = subprocess.run(
        [_bin_path("adjoint-hook-session-end")],
        input="",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert cp.returncode == 0
    # No output when short-circuiting.
    assert cp.stdout == ""


def test_hook_fails_open_on_malformed_input(adjoint_home: Path, project_dir: Path) -> None:
    """Exercises the real ``read_stdin_json`` → handler path, not the guard.

    We DROP the recursion marker here so the hook actually runs. ``pre_tool_use``
    is chosen because its M2 handler is still ``return None`` — no side effects
    to worry about — which lets us assert the fail-open contract cleanly.
    """
    _disable_flush(project_dir)
    env = {"ADJOINT_HOME": str(adjoint_home), "PATH": "/usr/bin:/bin"}
    cp = subprocess.run(
        [_bin_path("adjoint-hook-pre-tool-use")],
        input="not json at all {{{",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    # fail-open: never block the user, even on malformed JSON.
    assert cp.returncode == 0
    # Also enforce the "never blocks a tool" contract. Once M2 wires up real
    # policy output these assertions become meaningful; until then they serve
    # as a guard against regressions that start emitting a denial on junk.
    if cp.stdout.strip():
        try:
            parsed = json.loads(cp.stdout)
        except json.JSONDecodeError:
            return
        assert parsed.get("decision") != "block"
        assert parsed.get("permissionDecision") != "deny"
