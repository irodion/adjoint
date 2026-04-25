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
from ..store.sqlite import connect
from ._runtime import HookInput, run_hook

_PAYLOAD_STRING_CAP = 256


def _summarize_response(tool_response: Any) -> Any:
    """Trim bulky payloads before storing them in events.db.

    Long strings (>_PAYLOAD_STRING_CAP) collapse to ``{"_value_len": N}`` so a
    multi-megabyte tool stdout doesn't bloat the audit table — the transcript
    already has the full text. Dict values follow the same rule keyed as
    ``<k>_len``. Lists are mapped element-wise so list-of-dicts payloads
    (e.g. Glob results) stay summarised.
    """
    if isinstance(tool_response, dict):
        out: dict[str, Any] = {}
        for k, v in tool_response.items():
            if isinstance(v, str) and len(v) > _PAYLOAD_STRING_CAP:
                out[f"{k}_len"] = len(v)
            else:
                out[k] = v
        return out
    if isinstance(tool_response, list):
        return [_summarize_response(item) for item in tool_response]
    if isinstance(tool_response, str) and len(tool_response) > _PAYLOAD_STRING_CAP:
        return {"_value_len": len(tool_response)}
    return tool_response


def handle(hook_input: HookInput) -> dict[str, Any] | None:
    if not load_config(hook_input.cwd).audit.enabled:
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
        conn = connect()
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
