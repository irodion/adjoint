"""PostToolUse hook — append an audit row to the ``events`` table.

One SQLite INSERT per invocation. WAL mode + ``isolation_level=None`` means
writes are atomic and don't block readers. On any error (DB missing, locked,
migrations not yet run) we log WARN and return — losing an audit row is
always preferable to stalling the user's session.

Payload is trimmed: long stdout/stderr strings are replaced with ``*_len``
byte counts. The full text already lives in the session transcript; storing
it here would just bloat ``events.db`` and re-introduce redaction concerns.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..config import load_config
from ..log import get_logger, log_event
from ..paths import find_project_root
from ..store.sqlite import connect
from ._runtime import HookInput, run_hook

_PAYLOAD_STRING_CAP = 256

# Field names whose values are user-edited bodies — always replaced with a
# length summary regardless of size. A one-line ``.env`` secret or short
# token is exactly the kind of tool input we don't want in the audit DB,
# even though it falls under ``_PAYLOAD_STRING_CAP``. Covers Write / Edit /
# MultiEdit / NotebookEdit shapes and Read responses.
_BODY_FIELDS = frozenset({"content", "old_string", "new_string", "old_source", "new_source"})

# Audit writes need to fail fast if the DB is contended, not stall past the
# hook's 0.5 s deadline. If another adjoint process holds a lock, dropping
# this row is cheaper than blocking the user's tool invocation.
_AUDIT_BUSY_TIMEOUT_MS = 200


def _summarize_response(tool_response: Any) -> Any:
    """Trim bulky / sensitive payloads before storing them in events.db.

    A dict value gets replaced with ``<k>_len`` when either:

    * its key is a known body field (``content`` / ``old_string`` / etc.) —
      regardless of length, so short secrets don't leak; or
    * the value is a string longer than ``_PAYLOAD_STRING_CAP``.

    Dict / list children recurse so nested payloads — e.g. MultiEdit's
    ``{"edits": [{"old_string": ..., "new_string": ...}]}`` — are also
    trimmed. The transcript already has the full text, so there's no audit
    value in duplicating it here, only privacy and size cost.
    """
    if isinstance(tool_response, dict):
        out: dict[str, Any] = {}
        for k, v in tool_response.items():
            if isinstance(v, str) and (k in _BODY_FIELDS or len(v) > _PAYLOAD_STRING_CAP):
                out[f"{k}_len"] = len(v)
            elif isinstance(v, (dict, list)):
                out[k] = _summarize_response(v)
            else:
                out[k] = v
        return out
    if isinstance(tool_response, list):
        return [_summarize_response(item) for item in tool_response]
    if isinstance(tool_response, str) and len(tool_response) > _PAYLOAD_STRING_CAP:
        return {"_value_len": len(tool_response)}
    return tool_response


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    # Hook cwd may be <repo>/subdir; the project's audit opt-out lives in the
    # repo root's .adjoint/config.toml.
    project_root = find_project_root(hook_input.cwd) if hook_input.cwd else None
    if not load_config(project_root).audit.enabled:
        return None
    raw = hook_input.raw
    # ``tool_input`` for Write / Edit / MultiEdit carries the full file body or
    # patch text, which we deliberately do NOT want verbatim in the audit DB
    # (size + secret leakage). Same trimmer the response uses.
    payload = {
        "tool_name": raw.get("tool_name"),
        "tool_input": _summarize_response(raw.get("tool_input")),
        "tool_response": _summarize_response(raw.get("tool_response")),
        "duration_ms": raw.get("duration_ms"),
    }
    event_type = f"hook.{hook_input.hook_event_name or 'PostToolUse'}"
    try:
        conn = connect(busy_timeout_ms=_AUDIT_BUSY_TIMEOUT_MS)
        try:
            conn.execute(
                "INSERT INTO events(session_id, event_type, payload_json) VALUES(?, ?, ?)",
                (hook_input.session_id, event_type, json.dumps(payload, default=str)),
            )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — audit, never block
        log_event(get_logger("hook.post_tool_use"), "audit.write_failed", error=str(exc))
    return None


def main() -> int:
    return run_hook("post_tool_use", handle, timeout_s=0.5, fail_open=True)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
