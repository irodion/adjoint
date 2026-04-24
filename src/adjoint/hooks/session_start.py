"""SessionStart hook — inject knowledge/index.md as additionalContext.

Budget: 300ms (configured in bundled settings.hooks.json).
Fail-open: if anything goes wrong we return nothing and the session starts
normally — we must never block the user.
"""

from __future__ import annotations

import sys
from typing import Any

from ..config import load_config
from ..paths import user_paths
from ._runtime import HookInput, run_hook


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    cwd = hook_input.cwd
    if not cwd or not cwd.is_dir():
        return None

    cfg = load_config(cwd)
    if not cfg.memory.session_start_injection:
        return None

    pp = user_paths().project(cwd)
    if not pp.knowledge_index.is_file():
        return None

    text = pp.knowledge_index.read_text(encoding="utf-8")
    cap = max(1024, cfg.memory.index_max_bytes)
    encoded = text.encode("utf-8")
    if len(encoded) > cap:
        # render_index puts the highest-value sections (Active Action Items,
        # Recently Updated) at the TOP, so keep the head and cut at the last
        # newline inside the budget to avoid a mid-line break.
        head = encoded[:cap]
        newline = head.rfind(b"\n")
        if newline > 0:
            head = head[: newline + 1]
        text = head.decode("utf-8", errors="ignore")

    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }


def main() -> int:
    return run_hook("session_start", handle, timeout_s=0.3, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
