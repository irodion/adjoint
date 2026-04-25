"""Deny Write/Edit/NotebookEdit calls whose target is outside the project repo.

Ships disabled. Enable with:
    ln -s ~/.adjoint/policies/disabled/no_writes_outside_repo.py \\
          ~/.adjoint/policies/enabled/
"""

from __future__ import annotations

from pathlib import Path

from adjoint.policies.types import PolicyDecision, ToolUseContext

_FILE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def decide(ctx: ToolUseContext) -> PolicyDecision:
    if ctx.tool_name not in _FILE_TOOLS:
        return PolicyDecision(action="allow")
    target_str = ctx.tool_input.get("file_path") or ctx.tool_input.get("notebook_path")
    if not target_str:
        return PolicyDecision(action="allow")
    try:
        # A relative file_path means "under ctx.cwd", not "under the hook's
        # own working directory" — anchor before resolving.
        raw = Path(str(target_str)).expanduser()
        target = (raw if raw.is_absolute() else ctx.cwd / raw).resolve()
        target.relative_to(ctx.cwd.resolve())
    except ValueError:
        return PolicyDecision(
            action="deny",
            reason=f"write target {target_str!r} is outside repo {ctx.cwd}",
        )
    except OSError:
        return PolicyDecision(action="allow")
    return PolicyDecision(action="allow")
