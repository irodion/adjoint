"""Discover and run user-authored PreToolUse policies.

Each ``*.py`` in the enabled directory must expose ``decide(ctx) ->
PolicyDecision``. Files beginning with ``_`` are skipped so users can keep
shared helpers alongside the policies without them being invoked. Import
errors, exceptions, bad return types, and timeouts are all logged and then
treated as ``allow`` — adjoint is not a security boundary, so a broken
policy must never block the user.

Composition rule in ``compose``: the first ``deny`` wins; otherwise the first
``ask`` wins; otherwise ``allow``. ``modify`` and ``defer`` are reserved and
effectively collapse to ``allow`` here.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import threading
import time
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..log import get_logger, log_event
from .types import PolicyDecision, PolicyFn, ToolUseContext


def _log(msg: str, **extras: Any) -> None:
    """Log to the structured logger if available; swallow OSError if not.

    Discovery and execution must work in restricted environments — read-only
    home, sandbox CI, locked-down containers — where
    ``~/.adjoint/logs/adjoint.jsonl`` can't be opened. Without this guard the
    OSError bubbles out of ``get_logger`` (via ``configure``), the PreToolUse
    hook's outer try/except catches it, and every policy is silently disabled.
    """
    with contextlib.suppress(OSError):
        log_event(get_logger("policies.loader"), msg, **extras)


@contextlib.contextmanager
def _on_sys_path(path: Path) -> Iterator[None]:
    """Scope ``path`` at the front of ``sys.path`` for the duration of the block.

    Lets each policy module's top-level ``from _helper import RULES`` resolve a
    sibling file in the same directory. Scoped rather than permanent so back-
    to-back ``discover_policies`` calls in tests don't pollute ``sys.path``.
    """
    entry = str(path)
    sys.path.insert(0, entry)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(entry)


# Per-policy import budget. A policy that blocks at module top-level
# (``import time; time.sleep(5)``) would otherwise hang ``exec_module``
# until the outer 2 s hook SIGALRM fires, skipping every other policy and
# fail-opening the call. 1 s is generous for legitimate imports while
# still leaving room for the outer deadline if multiple policies misbehave.
_IMPORT_TIMEOUT_S = 1.0


def _import_module_with_timeout(
    spec: importlib.machinery.ModuleSpec,
    module: Any,
    label: str,
    timeout_s: float,
) -> bool:
    """Run ``spec.loader.exec_module(module)`` on a daemon thread with a deadline.

    Returns True iff the import completed in time and raised nothing. The
    daemon thread is left running on timeout — the hook process exits soon
    enough that the leak doesn't matter, and aborting an executing import
    cleanly isn't possible without process-level controls.
    """
    # Caller has already verified ``spec.loader is not None``; bind to a
    # local so mypy is happy without a runtime assert (which Bandit flags).
    loader = spec.loader
    if loader is None:  # pragma: no cover — defence in depth
        return False
    error: list[BaseException] = []

    def _target() -> None:
        try:
            loader.exec_module(module)
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    t = threading.Thread(target=_target, name=f"adjoint-policy-import-{label}", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        _log("policy.import.timeout", path=label, timeout_s=timeout_s)
        return False
    if error:
        _log(
            "policy.import.error",
            path=label,
            error=str(error[0]),
            traceback="".join(traceback.format_exception(error[0])),
        )
        return False
    return True


def discover_policies(policies_dir: Path) -> list[tuple[str, PolicyFn]]:
    """Import every ``*.py`` in ``policies_dir`` (skipping ``_*.py``).

    Returns ``[(name, decide_fn), ...]`` sorted by filename so composition is
    deterministic. Policies may import sibling helpers at module top-level
    (e.g. ``from _helper import RULES``); ``policies_dir`` is temporarily
    prepended to ``sys.path`` during discovery to make that resolution work.
    Each module's ``exec_module`` runs with a wall-clock deadline so a
    blocking top-level import doesn't stall the entire hook. Modules that
    fail to import, time out, or lack a ``decide`` callable are logged and
    omitted.
    """
    if not policies_dir.is_dir():
        return []
    out: list[tuple[str, PolicyFn]] = []
    with _on_sys_path(policies_dir):
        for path in sorted(policies_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"adjoint_policy_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
            except Exception as exc:  # noqa: BLE001
                _log("policy.import.error", path=str(path), error=str(exc))
                continue
            if spec is None or spec.loader is None:
                _log("policy.import.skip", path=str(path), reason="no-spec")
                continue
            module = importlib.util.module_from_spec(spec)
            # Register before ``exec_module`` so the policy can reference
            # itself (e.g., dataclasses' ``__module__`` lookup) and so
            # circular imports between sibling policy files terminate.
            # Standard pattern from importlib docs.
            sys.modules[mod_name] = module
            if not _import_module_with_timeout(
                spec, module, str(path), timeout_s=_IMPORT_TIMEOUT_S
            ):
                sys.modules.pop(mod_name, None)
                continue
            decide = getattr(module, "decide", None)
            if not callable(decide):
                _log("policy.import.skip", path=str(path), reason="no-decide")
                continue
            out.append((path.stem, decide))
    return out


def _run_one(
    name: str,
    fn: PolicyFn,
    ctx: ToolUseContext,
    timeout_s: float,
) -> PolicyDecision | None:
    """Run one policy with a wall-clock deadline; fail-open on timeout/error.

    Uses a **daemon** thread. ``ThreadPoolExecutor`` workers are non-daemon and
    its ``atexit`` handler joins every still-running worker, so a hung policy
    would prevent the hook process from exiting and defeat the fail-open
    timeout. A daemon thread is killed when the hook process exits, so a
    wedged policy can't keep the interpreter alive.
    """
    outcome: list[object] = []

    def _target() -> None:
        try:
            outcome.append(fn(ctx))
        except BaseException as exc:  # noqa: BLE001 — capture for logging
            outcome.append(exc)

    t = threading.Thread(target=_target, name=f"adjoint-policy-{name}", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        _log("policy.timeout", policy=name, timeout_s=timeout_s)
        return None
    if not outcome:
        _log("policy.no_result", policy=name)
        return None
    result = outcome[0]
    if isinstance(result, BaseException):
        _log(
            "policy.error",
            policy=name,
            error=str(result),
            traceback="".join(traceback.format_exception(result)),
        )
        return None
    if not isinstance(result, PolicyDecision):
        _log("policy.bad_return", policy=name, returned=type(result).__name__)
        return None
    return result


def compose(decisions: list[PolicyDecision]) -> PolicyDecision:
    """Pick the strongest signal: deny > ask > allow > defer.

    Empty input or input made entirely of reserved values (``modify`` /
    ``defer``) produces ``defer`` — the "no decisive opinion" sentinel. The
    PreToolUse hook treats ``defer`` as fall-through (don't emit a
    ``permissionDecision``), which lets Claude Code's normal permission flow
    proceed. ``allow`` is reserved for the case where at least one policy
    *explicitly* approved the call, so we can proactively skip the normal
    user-confirm UI for tools that would otherwise prompt.
    """
    for d in decisions:
        if d.action == "deny":
            return d
    for d in decisions:
        if d.action == "ask":
            return d
    for d in decisions:
        if d.action == "allow":
            return d
    return PolicyDecision(action="defer")


def run_policies(
    ctx: ToolUseContext,
    policies: list[tuple[str, PolicyFn]],
    timeout_ms: int,
    *,
    total_budget_s: float | None = None,
) -> PolicyDecision:
    """Run policies sequentially with ``timeout_ms`` each, compose the result.

    Two protections against the outer hook deadline:

    1. ``total_budget_s`` (when set) caps the entire run. Each per-policy
       timeout is clamped to whatever's left in the budget, and the loop
       breaks once exhausted. Without this, ``timeout_ms × N`` could exceed
       the outer 2 s SIGALRM in ``run_hook`` — the alarm would fire inside
       the loop, skip ``compose``, and fail-open silently promote a denied
       tool call to allow.

    2. Short-circuit on ``deny`` only. ``deny`` is the strongest outcome under
       the compose rule, so no later policy can change it. We deliberately do
       *not* short-circuit on ``ask``: a later policy's ``deny`` must be able
       to override an earlier ``ask`` per the documented composition order.
    """
    timeout_s = max(timeout_ms / 1000.0, 0.001)
    deadline = time.monotonic() + total_budget_s if total_budget_s is not None else None
    collected: list[PolicyDecision] = []
    for name, fn in policies:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _log("policies.budget_exhausted", remaining=len(policies) - len(collected))
                break
            per_call = min(timeout_s, remaining)
        else:
            per_call = timeout_s
        decision = _run_one(name, fn, ctx, per_call)
        if decision is None:
            continue
        collected.append(decision)
        if decision.action == "deny":
            break
    return compose(collected)
