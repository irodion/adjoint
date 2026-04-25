"""Reference example: log every tool invocation to stderr, never block.

Use as a starting point for your own policies. ``decide`` receives a
``ToolUseContext`` with ``tool_name``, ``tool_input``, ``session_id``, and the
repo ``cwd``; it must return a ``PolicyDecision`` whose ``action`` is one of
``allow``, ``deny``, or ``ask``.

Ships disabled. Enable with:
    ln -s ~/.adjoint/policies/disabled/log_only.py \\
          ~/.adjoint/policies/enabled/
"""

from __future__ import annotations

import sys

from adjoint.policies.types import PolicyDecision, ToolUseContext


def decide(ctx: ToolUseContext) -> PolicyDecision:
    print(
        f"[policy.log_only] {ctx.tool_name} session={ctx.session_id or '-'}",
        file=sys.stderr,
        flush=True,
    )
    return PolicyDecision(action="allow")
