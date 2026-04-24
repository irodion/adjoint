"""SessionEnd hook — fire a detached flush subprocess and return."""

from __future__ import annotations

import sys
from typing import Any

from ..config import load_config
from ._flush_spawn import spawn_flush
from ._runtime import HookInput, run_hook


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    if not hook_input.cwd:
        return None
    cfg = load_config(hook_input.cwd)
    if not cfg.memory.flush_on_session_end:
        return None
    spawn_flush(
        transcript_path=hook_input.transcript_path,
        project_cwd=hook_input.cwd,
        reason="session_end",
        session_id=hook_input.session_id,
    )
    return None


def main() -> int:
    return run_hook("session_end", handle, timeout_s=0.5, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
