"""PostToolUse hook — append row to events table (audit + tracing)."""

from __future__ import annotations

import sys
from typing import Any

from ._runtime import HookInput, run_hook


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    # TODO(M2): insert event row (direct SQLite WAL when daemon absent).
    return None


def main() -> int:
    return run_hook("post_tool_use", handle, timeout_s=0.1, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
