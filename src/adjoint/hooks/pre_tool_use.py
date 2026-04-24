"""PreToolUse hook — load user policies, compose decisions, fail-open on timeout.

M0 scaffold: no policies loaded yet. M2 wires up ``policies/loader.py``.
adjoint is NOT a security boundary — on timeout or error we return ``allow``.
"""

from __future__ import annotations

import sys
from typing import Any

from ._runtime import HookInput, run_hook


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    # TODO(M2): load ~/.adjoint/policies/enabled/*.py, compose decisions.
    return None


def main() -> int:
    return run_hook("pre_tool_use", handle, timeout_s=2.0, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
