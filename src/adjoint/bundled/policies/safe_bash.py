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
    # rm short flags (-r, -f, -rf, etc.) OR GNU long flags --recursive / --force.
    (re.compile(r"\brm\s+-\w*[rf]\w*\b"), "rm -r/-f"),
    (re.compile(r"\brm\b[^;]*?--(?:recursive|force)\b"), "rm --recursive/--force"),
    (re.compile(r"(?:^|\s)sudo(?:\s|$)"), "sudo"),
    # curl/wget piped into a shell, with optional ``sudo`` on the consumer side.
    (re.compile(r"\bcurl\b[^|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|fish)\b"), "curl | sh"),
    (re.compile(r"\bwget\b[^|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|fish)\b"), "wget | sh"),
    (re.compile(r"\bchmod\s+-?\w*777\b"), "chmod 777"),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    # ``dd of=/dev/...`` regardless of argument order — historically the
    # standard exploit pattern is ``dd if=/dev/zero of=/dev/sda``, but
    # ``of=`` may legally precede ``if=``.
    (re.compile(r"\bdd\b[^;]*?\bof=/dev/"), "dd of=/dev/*"),
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
