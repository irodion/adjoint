"""Policy types shared between the loader, the PreToolUse hook, and user policies.

M2 ships ``allow`` / ``deny`` / ``ask``. The ``modify`` and ``defer`` values
are kept in the ``PolicyAction`` Literal as reserved — Claude Code's
PreToolUse hook has no first-class ``updatedInput`` channel today, so a
``modify`` decision collapses to ``allow`` during composition. The schema is
stable for when the surface expands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

PolicyAction = Literal["allow", "deny", "modify", "ask", "defer"]


class PolicyDecision(BaseModel):
    action: PolicyAction
    reason: str | None = None
    updated_input: dict[str, Any] | None = None
    ask_user: bool = False


@dataclass(frozen=True)
class ToolUseContext:
    tool_name: str
    # Read-only at the type layer. ``pre_tool_use`` wraps the raw dict in a
    # ``types.MappingProxyType`` so an earlier policy can't mutate the view a
    # later one receives.
    tool_input: Mapping[str, Any]
    cwd: Path
    session_id: str | None
    transcript_path: Path | None


class PolicyFn(Protocol):
    def __call__(self, ctx: ToolUseContext) -> PolicyDecision: ...
