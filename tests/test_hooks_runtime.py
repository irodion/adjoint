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


def test_every_hook_exits_0_on_valid_json(adjoint_home):  # type: ignore[no-untyped-def]
    stdin_payload = json.dumps(
        {
            "session_id": "test-session",
            "transcript_path": "/tmp/nonexistent.jsonl",
            "cwd": "/tmp",
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


def test_hook_short_circuits_on_recursion_marker(adjoint_home):  # type: ignore[no-untyped-def]
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


def test_hook_fails_open_on_malformed_input(adjoint_home):  # type: ignore[no-untyped-def]
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
