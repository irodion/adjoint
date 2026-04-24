"""JSON-structured logging with daily rotation + recursion guard helpers.

All logs go to ``~/.adjoint/logs/adjoint.jsonl`` (one JSON object per line),
rotated daily, 14-day retention. Hooks and the daemon both log through here.

The recursion guard uses the ``CLAUDE_INVOKED_BY`` env var to prevent
adjoint-spawned subprocesses (flush, compile, query, second_opinion) from
re-triggering the hooks that produced them.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Literal, get_args

from .paths import user_paths

RECURSION_ENV_VAR = "CLAUDE_INVOKED_BY"

# Any adjoint subprocess sets CLAUDE_INVOKED_BY to one of these values so that
# nested hook fires short-circuit. Typed as a Literal so callers that mis-spell
# a tag fail at type-check time.
RecursionTag = Literal[
    "adjoint",
    "adjoint_flush",
    "adjoint_compile",
    "adjoint_query",
    "adjoint_second_opinion",
    "adjoint_variants",
    "adjoint_run",
]
RECURSION_VALUES: frozenset[str] = frozenset(get_args(RecursionTag))


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extras = getattr(record, "extras", None)
        if isinstance(extras, dict):
            for k, v in extras.items():
                payload.setdefault(k, v)
        return json.dumps(payload, default=str, ensure_ascii=False)


_configured = False


def configure(level: str = "INFO") -> None:
    """Idempotent root-logger configuration."""
    global _configured
    if _configured:
        return
    paths = user_paths()
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    handler = TimedRotatingFileHandler(
        paths.log_file,
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        utc=True,
    )
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger("adjoint")
    root.setLevel(level.upper())
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(f"adjoint.{name}")


def log_event(logger: logging.Logger, msg: str, **extras: Any) -> None:
    """Shorthand for ``logger.info(msg, extra={'extras': extras})``."""
    logger.info(msg, extra={"extras": extras})


def is_recursive_invocation() -> bool:
    """True if we are being invoked by another adjoint process.

    Hooks MUST short-circuit when this is true to prevent e.g. a flush subprocess
    spawning Claude which re-fires SessionEnd which spawns another flush …
    """
    return os.environ.get(RECURSION_ENV_VAR, "") in RECURSION_VALUES


def mark_invoked_by(tag: RecursionTag) -> dict[str, str]:
    """Return env overlay to pass to a subprocess we're about to spawn."""
    return {RECURSION_ENV_VAR: tag}


def child_env(tag: RecursionTag) -> dict[str, str]:
    """Full env for a child subprocess, with the recursion guard injected."""
    env = dict(os.environ)
    env.update(mark_invoked_by(tag))
    return env


def log_path() -> Path:
    return user_paths().log_file
