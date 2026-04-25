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
from types import MappingProxyType
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
    # Recursively immutable. ``freeze_tool_input`` wraps every dict in a
    # ``MappingProxyType`` and converts every list to a tuple, so an earlier
    # policy can't mutate the view a later policy receives. Lists become
    # tuples — policies that do ``isinstance(x, list)`` need to allow
    # ``Sequence`` instead, but iteration / indexing / ``len`` still work.
    tool_input: Mapping[str, Any]
    # Project root, not the literal session cwd. ``pre_tool_use`` walks up from
    # the hook payload's cwd to the nearest ``.adjoint/`` or ``.git`` so
    # repo-boundary policies (``no_writes_outside_repo``) work for nested
    # sessions launched from ``<repo>/subdir/``.
    cwd: Path
    session_id: str | None
    transcript_path: Path | None


class PolicyFn(Protocol):
    def __call__(self, ctx: ToolUseContext) -> PolicyDecision: ...


def freeze_tool_input(value: Any) -> Any:
    """Recursively freeze a ``tool_input`` payload for cross-policy isolation.

    Dicts (and ``Mapping`` subtypes) become ``MappingProxyType``; lists become
    tuples. Other values are returned as-is — strings, numbers, bytes, and
    ``None`` are already immutable. The returned structure is safe to share
    across sequentially-invoked policies without one being able to alter the
    view another sees.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze_tool_input(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze_tool_input(v) for v in value)
    return value
