"""Spawn a detached ``adjoint memory flush`` subprocess.

Shared by the SessionEnd and PreCompact hooks. The hook returns immediately
while flush runs in the background, so the user's IDE exit is never blocked.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ..log import child_env, get_logger, log_event


def spawn_flush(
    *,
    transcript_path: Path | None,
    project_cwd: Path | None,
    reason: str,
    session_id: str | None,
) -> bool:
    """Fire-and-forget. Returns True if spawn succeeded, False otherwise."""
    logger = get_logger("hook.flush_spawn")

    adjoint_bin = shutil.which("adjoint")
    if not adjoint_bin:
        # Fall back to the same interpreter we're running under — the CLI is
        # installed as a console script in the same venv.
        adjoint_bin = str(Path(sys.executable).parent / "adjoint")
    if not Path(adjoint_bin).exists():
        log_event(logger, "flush_spawn.skip.no_binary", adjoint_bin=adjoint_bin)
        return False

    if not transcript_path or not project_cwd:
        log_event(
            logger,
            "flush_spawn.skip.missing_args",
            transcript=str(transcript_path),
            cwd=str(project_cwd),
        )
        return False

    argv = [
        adjoint_bin,
        "memory",
        "flush",
        "--transcript",
        str(transcript_path),
        "--project",
        str(project_cwd),
        "--reason",
        reason,
    ]
    if session_id:
        argv += ["--session-id", session_id]

    try:
        subprocess.Popen(  # noqa: S603 — argv list, shell=False
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env("adjoint_flush"),
            cwd=str(project_cwd),
            close_fds=True,
            shell=False,
        )
    except OSError as exc:
        log_event(logger, "flush_spawn.error", error=str(exc))
        return False

    log_event(logger, "flush_spawn.ok", reason=reason, cwd=str(project_cwd))
    return True
