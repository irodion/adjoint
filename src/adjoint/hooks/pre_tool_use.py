"""PreToolUse hook — load user policies, compose decisions, fail-open on timeout.

Discovers ``*.py`` files in the configured policies directory (default
``~/.adjoint/policies/enabled/``), runs each with a per-policy timeout
(``policies.timeout_ms``), composes their decisions (deny > ask > allow),
and emits a Claude Code ``permissionDecision`` response. adjoint is NOT a
security boundary — on timeout or exception we return ``allow`` (None here
means allow).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..config import PoliciesConfig, load_config
from ..paths import find_project_root, user_paths
from ..policies.loader import discover_policies, run_policies
from ..policies.types import ToolUseContext, freeze_tool_input
from ._runtime import HookInput, run_hook

_DEFAULT_REASON = {
    "deny": "policy denied",
    "ask": "policy requests confirmation",
    "allow": "policy approved",
}

# The literal default string in ``PoliciesConfig.dir``. Matching this exactly
# is how we detect "user did not override" and route to ``user_paths()``
# instead — the latter honors ``ADJOINT_HOME`` where naïve ``expanduser``
# does not.
_DEFAULT_DIR = PoliciesConfig.model_fields["dir"].default

# Budget for the policy loop. The outer ``run_hook`` deadline is 2.0 s; we
# leave ~500 ms slack for ``load_config``, ``discover_policies`` imports,
# composition, and JSON emit. Without this clamp, N slow policies × the
# per-policy timeout could exceed the SIGALRM and silently fail-open.
_RUN_POLICIES_BUDGET_S = 1.5


def _resolve_policies_dir(configured: str, project_cwd: Path) -> Path:
    """Map ``cfg.policies.dir`` to a real filesystem path.

    When ``configured`` matches the literal default in ``PoliciesConfig.dir``,
    we treat that as "user did not override" and route through
    ``user_paths().policies_enabled`` — that path honors ``ADJOINT_HOME``,
    which a naïve ``Path(default).expanduser()`` would not. Explicit overrides
    are ``expanduser``'d; relative overrides like ``[policies] dir = "custompol"``
    are anchored against ``project_cwd`` so the lookup doesn't depend on
    whatever directory Claude Code happened to spawn the hook from.

    Comparison is path-based (with whitespace stripped) so pasted variants of
    the default — trailing slash, surrounding whitespace — still route through
    ``user_paths()``. A naïve string equality would let those variants bypass
    ``ADJOINT_HOME`` and resolve against the real OS home instead.
    """
    if Path(configured.strip()) == Path(_DEFAULT_DIR):
        return user_paths().policies_enabled
    expanded = Path(configured).expanduser()
    if expanded.is_absolute():
        return expanded
    return (project_cwd / expanded).resolve()


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    cwd = hook_input.cwd
    if not cwd:
        return None

    # Hooks may launch from <repo>/subdir; everything keyed on "the project"
    # — config, policies dir, repo-boundary checks — must use the actual
    # root, not the literal session cwd.
    project_root = find_project_root(cwd)
    cfg = load_config(project_root)
    policies = discover_policies(_resolve_policies_dir(cfg.policies.dir, project_root))
    if not policies:
        return None

    raw = hook_input.raw
    ctx = ToolUseContext(
        tool_name=str(raw.get("tool_name", "")),
        tool_input=freeze_tool_input(raw.get("tool_input", {}) or {}),
        cwd=project_root,
        session_id=hook_input.session_id,
        transcript_path=hook_input.transcript_path,
    )
    decision = run_policies(
        ctx, policies, cfg.policies.timeout_ms, total_budget_s=_RUN_POLICIES_BUDGET_S
    )

    if decision.action in ("deny", "ask", "allow"):
        # ``allow`` proactively approves the call (skipping Claude Code's
        # normal permission UI). Falling through here would leave that flow
        # in place, which defeats the documented intent of policies like
        # ``safe_bash`` that allow non-dangerous Bash commands.
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision.action,
                "permissionDecisionReason": (decision.reason or _DEFAULT_REASON[decision.action]),
            }
        }
    # ``defer`` (no decisive opinion) and reserved ``modify`` fall through
    # to Claude Code's normal flow.
    return None


def main() -> int:
    return run_hook("pre_tool_use", handle, timeout_s=2.0, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
