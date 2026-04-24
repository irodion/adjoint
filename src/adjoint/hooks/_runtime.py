"""Shared hook runtime: stdin parsing, recursion guard, daemon detection, timeout.

Claude Code invokes each hook binary with a JSON payload on stdin containing
``session_id``, ``transcript_path``, ``cwd``, ``hook_event_name``, and
event-specific fields. The hook writes a JSON response to stdout (optional)
and exits 0 on success, 2 to block the action (PreToolUse only), or non-zero
to surface a non-blocking warning.

Hooks MUST respect the recursion guard — if ``CLAUDE_INVOKED_BY`` indicates
we're already inside an adjoint subprocess, exit 0 immediately to prevent
infinite re-entry.
"""

from __future__ import annotations

import json
import signal
import socket
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..log import get_logger, is_recursive_invocation, log_event
from ..paths import user_paths


@dataclass
class HookInput:
    session_id: str | None = None
    transcript_path: Path | None = None
    cwd: Path | None = None
    hook_event_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, data: dict[str, Any]) -> HookInput:
        tp = data.get("transcript_path")
        cwd = data.get("cwd")
        return cls(
            session_id=data.get("session_id"),
            transcript_path=Path(tp) if tp else None,
            cwd=Path(cwd) if cwd else None,
            hook_event_name=data.get("hook_event_name"),
            raw=data,
        )


def read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def daemon_running() -> bool:
    sock_path = user_paths().daemon_sock
    if not sock_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            s.connect(str(sock_path))
        return True
    except (TimeoutError, OSError):
        return False


def write_response(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


class HookTimeout(RuntimeError):
    pass


@contextmanager
def deadline(seconds: float) -> Iterator[None]:
    """POSIX SIGALRM-based hard deadline. seconds may be fractional."""

    def _handler(signum: int, frame: Any) -> None:
        raise HookTimeout(f"hook exceeded {seconds:g}s deadline")

    whole = max(1, int(round(seconds)))
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(whole)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def run_hook(
    name: str,
    handler: Callable[[HookInput], dict[str, Any] | None],
    *,
    timeout_s: float,
    fail_open: bool = True,
) -> int:
    """Run a hook handler with shared plumbing.

    ``handler`` receives a parsed ``HookInput`` and returns either a dict
    (written to stdout as JSON) or ``None``. Exceptions, timeouts, and
    recursive invocations all exit 0 when ``fail_open`` is True — adjoint is
    NOT a security boundary, so a broken hook must never block the user.
    """
    logger = get_logger(f"hook.{name}")

    if is_recursive_invocation():
        return 0

    try:
        data = read_stdin_json()
        hook_input = HookInput.parse(data)
        with deadline(timeout_s):
            result = handler(hook_input)
        if result is not None:
            write_response(result)
        log_event(
            logger,
            "hook.ok",
            hook=name,
            session_id=hook_input.session_id,
            event=hook_input.hook_event_name,
        )
        return 0
    except HookTimeout as exc:
        log_event(logger, "hook.timeout", hook=name, error=str(exc))
        return 0 if fail_open else 1
    except Exception as exc:  # noqa: BLE001
        log_event(
            logger,
            "hook.error",
            hook=name,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        return 0 if fail_open else 1
