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
import traceback
from collections.abc import Iterator
from pathlib import Path

from ..log import get_logger, log_event
from .types import PolicyDecision, PolicyFn, ToolUseContext


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


def discover_policies(policies_dir: Path) -> list[tuple[str, PolicyFn]]:
    """Import every ``*.py`` in ``policies_dir`` (skipping ``_*.py``).

    Returns ``[(name, decide_fn), ...]`` sorted by filename so composition is
    deterministic. Policies may import sibling helpers at module top-level
    (e.g. ``from _helper import RULES``); ``policies_dir`` is temporarily
    prepended to ``sys.path`` during discovery to make that resolution work.
    Modules that fail to import or lack a ``decide`` callable are logged and
    omitted.
    """
    if not policies_dir.is_dir():
        return []
    logger = get_logger("policies.loader")
    out: list[tuple[str, PolicyFn]] = []
    with _on_sys_path(policies_dir):
        for path in sorted(policies_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"adjoint_policy_{path.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                if spec is None or spec.loader is None:
                    log_event(logger, "policy.import.skip", path=str(path), reason="no-spec")
                    continue
                module = importlib.util.module_from_spec(spec)
                # Register before ``exec_module`` so the policy can reference
                # itself (e.g., dataclasses' ``__module__`` lookup) and so
                # circular imports between sibling policy files terminate.
                # Standard pattern from importlib docs.
                sys.modules[mod_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(mod_name, None)
                    raise
            except Exception as exc:  # noqa: BLE001 — fail-open on import error
                log_event(
                    logger,
                    "policy.import.error",
                    path=str(path),
                    error=str(exc),
                    traceback=traceback.format_exc(),
                )
                continue
            decide = getattr(module, "decide", None)
            if not callable(decide):
                log_event(logger, "policy.import.skip", path=str(path), reason="no-decide")
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
    logger = get_logger("policies.loader")
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
        log_event(logger, "policy.timeout", policy=name, timeout_s=timeout_s)
        return None
    if not outcome:
        log_event(logger, "policy.no_result", policy=name)
        return None
    result = outcome[0]
    if isinstance(result, BaseException):
        log_event(
            logger,
            "policy.error",
            policy=name,
            error=str(result),
            traceback="".join(traceback.format_exception(result)),
        )
        return None
    if not isinstance(result, PolicyDecision):
        log_event(logger, "policy.bad_return", policy=name, returned=type(result).__name__)
        return None
    return result


def compose(decisions: list[PolicyDecision]) -> PolicyDecision:
    """First deny wins; otherwise first ask; otherwise allow."""
    for d in decisions:
        if d.action == "deny":
            return d
    for d in decisions:
        if d.action == "ask":
            return d
    return PolicyDecision(action="allow")


def run_policies(
    ctx: ToolUseContext,
    policies: list[tuple[str, PolicyFn]],
    timeout_ms: int,
) -> PolicyDecision:
    """Run every policy with ``timeout_ms`` each, compose the surviving results."""
    timeout_s = max(timeout_ms / 1000.0, 0.001)
    collected: list[PolicyDecision] = []
    for name, fn in policies:
        decision = _run_one(name, fn, ctx, timeout_s)
        if decision is not None:
            collected.append(decision)
    return compose(collected)
