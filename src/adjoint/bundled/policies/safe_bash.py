"""Ask the user to confirm potentially dangerous Bash commands.

Returns ``action="ask"`` (not ``deny``) so legitimate usage still works with a
single confirm click. Fork this file and change the returns to ``deny`` for a
stricter stance.

Ships disabled. Enable with:
    ln -s ~/.adjoint/policies/disabled/safe_bash.py \\
          ~/.adjoint/policies/enabled/
"""

from __future__ import annotations

import re

from adjoint.policies.types import PolicyDecision, ToolUseContext

_DANGER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+-\w*[rf]\w*\s"), "rm -r/-f"),
    (re.compile(r"(?:^|\s)sudo(?:\s|$)"), "sudo"),
    (re.compile(r"\bcurl\b[^|]*\|\s*(?:sh|bash|zsh|fish)\b"), "curl | sh"),
    (re.compile(r"\bwget\b[^|]*\|\s*(?:sh|bash|zsh|fish)\b"), "wget | sh"),
    (re.compile(r"\bchmod\s+-?\w*777\b"), "chmod 777"),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\bdd\s+if=[^ ]+\s+of=/dev/"), "dd of=/dev/*"),
    (re.compile(r"\bmkfs\.\w+"), "mkfs"),
)


def decide(ctx: ToolUseContext) -> PolicyDecision:
    if ctx.tool_name != "Bash":
        return PolicyDecision(action="allow")
    cmd = str(ctx.tool_input.get("command", ""))
    if not cmd:
        return PolicyDecision(action="allow")
    for pattern, label in _DANGER_PATTERNS:
        if pattern.search(cmd):
            return PolicyDecision(
                action="ask",
                reason=f"safe_bash: command contains {label!r}; please confirm",
            )
    return PolicyDecision(action="allow")
