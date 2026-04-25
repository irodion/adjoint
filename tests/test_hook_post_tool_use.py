"""End-to-end test for the PostToolUse hook — one row per invocation."""

from __future__ import annotations

import json
from pathlib import Path


def _install(project_dir: Path) -> None:
    """Run migrations so events.db and its schema exist."""
    from adjoint.install import apply_install, build_install_plan

    plan, merged = build_install_plan("project", project_dir)
    apply_install(plan, merged)


def _fetch_events() -> list[dict]:
    from adjoint.store.sqlite import connect

    conn = connect()
    try:
        rows = list(
            conn.execute("SELECT session_id, event_type, payload_json FROM events ORDER BY id ASC")
        )
    finally:
        conn.close()
    return [
        {
            "session_id": r["session_id"],
            "event_type": r["event_type"],
            "payload": json.loads(r["payload_json"]),
        }
        for r in rows
    ]


def test_post_tool_use_writes_event_row(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    _install(project_dir)
    stdin = json.dumps(
        {
            "session_id": "sess-xyz",
            "transcript_path": str(project_dir / "nope.jsonl"),
            "cwd": str(project_dir),
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_response": {"exit_code": 0, "stdout": "file.txt", "stderr": ""},
            "duration_ms": 42,
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
    assert cp.stdout == ""

    rows = _fetch_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "sess-xyz"
    assert row["event_type"] == "hook.PostToolUse"
    assert row["payload"]["tool_name"] == "Bash"
    assert row["payload"]["tool_input"] == {"command": "ls"}
    assert row["payload"]["duration_ms"] == 42


def test_post_tool_use_strips_short_body_fields(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """A one-line .env secret is short but is exactly the kind of body we
    must not persist verbatim. Body field names are always summarised."""
    _install(project_dir)
    secret = "OPENAI_API_KEY=sk-abc123"  # well under _PAYLOAD_STRING_CAP
    stdin = json.dumps(
        {
            "session_id": "s",
            "cwd": str(project_dir),
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/.env", "content": secret},
            "tool_response": {"ok": True},
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
    rows = _fetch_events()
    ti = rows[0]["payload"]["tool_input"]
    assert "content" not in ti, "short body must not be stored verbatim"
    assert ti.get("content_len") == len(secret)
    assert ti["file_path"] == "/tmp/.env"


def test_post_tool_use_drops_row_under_db_contention(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """If another writer holds an exclusive lock, the audit insert must
    fail-fast (within ~0.2 s) instead of stalling for the default 5 s."""
    import sqlite3
    import time

    _install(project_dir)
    blocker = sqlite3.connect(str(adjoint_home / "events.db"), timeout=30.0)
    blocker.isolation_level = None
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute(
        "INSERT INTO events(session_id, event_type, payload_json) VALUES(?, ?, ?)",
        ("blocker", "test", "{}"),
    )
    try:
        stdin = json.dumps(
            {
                "session_id": "s",
                "cwd": str(project_dir),
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_response": {"ok": True},
            }
        )
        t0 = time.monotonic()
        cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
        elapsed = time.monotonic() - t0
        assert cp.returncode == 0
        # Generous upper bound — well below the prior ~5.4 s reported by the
        # reviewer. Subprocess cold-start dominates; the SQLite wait itself
        # should hit the 200 ms ceiling.
        assert elapsed < 2.0, f"audit hook stalled for {elapsed:.2f}s"
    finally:
        blocker.rollback()
        blocker.close()


def test_post_tool_use_summarizes_long_tool_input(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """Write/Edit tool_input bodies must not land verbatim in events.db."""
    _install(project_dir)
    big = "secret-content " * 200  # >> _PAYLOAD_STRING_CAP
    stdin = json.dumps(
        {
            "session_id": "s",
            "cwd": str(project_dir),
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x.txt", "content": big},
            "tool_response": {"ok": True},
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
    rows = _fetch_events()
    assert len(rows) == 1
    ti = rows[0]["payload"]["tool_input"]
    assert ti["file_path"] == "/tmp/x.txt"
    assert "content" not in ti, "raw content must not be persisted"
    assert ti.get("content_len") == len(big)


def test_post_tool_use_summarizes_long_stdout(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    _install(project_dir)
    big = "x" * 1024
    stdin = json.dumps(
        {
            "session_id": "s",
            "cwd": str(project_dir),
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "echo"},
            "tool_response": {"exit_code": 0, "stdout": big, "stderr": ""},
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
    rows = _fetch_events()
    assert len(rows) == 1
    tr = rows[0]["payload"]["tool_response"]
    assert tr.get("stdout_len") == 1024
    assert "stdout" not in tr
    assert tr["stderr"] == ""


def test_post_tool_use_summarizes_nested_multiedit_payload(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """MultiEdit's tool_input nests long strings inside ``edits`` list of dicts.

    Without recursive summarization the long old_string/new_string bodies
    landed verbatim in events.db.
    """
    _install(project_dir)
    big = "secret-old-content " * 200  # >> _PAYLOAD_STRING_CAP
    stdin = json.dumps(
        {
            "session_id": "s",
            "cwd": str(project_dir),
            "hook_event_name": "PostToolUse",
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": "/tmp/x.txt",
                "edits": [
                    {"old_string": big, "new_string": big},
                    {"old_string": "short", "new_string": "also short"},
                ],
            },
            "tool_response": {"ok": True},
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
    rows = _fetch_events()
    assert len(rows) == 1
    edits = rows[0]["payload"]["tool_input"]["edits"]
    assert len(edits) == 2
    # Body fields are always summarised — short or long.
    assert edits[0].get("old_string_len") == len(big)
    assert edits[0].get("new_string_len") == len(big)
    assert "old_string" not in edits[0]
    assert "new_string" not in edits[0]
    assert edits[1].get("old_string_len") == len("short")
    assert edits[1].get("new_string_len") == len("also short")
    assert "old_string" not in edits[1]


def test_summarize_response_handles_str_and_list() -> None:
    from adjoint.hooks.post_tool_use import _PAYLOAD_STRING_CAP, _summarize_response

    short = "x" * (_PAYLOAD_STRING_CAP // 2)
    long = "y" * (_PAYLOAD_STRING_CAP * 2)

    # Bare long string → ``{"_value_len": N}``.
    summarized = _summarize_response(long)
    assert summarized == {"_value_len": len(long)}

    # Bare short string → unchanged.
    assert _summarize_response(short) == short

    # List of mixed payloads — recurse element-wise.
    summarized_list = _summarize_response(
        [
            {"path": "a.txt", "preview": long},
            short,
            long,
        ]
    )
    assert summarized_list[0] == {"path": "a.txt", "preview_len": len(long)}
    assert summarized_list[1] == short
    assert summarized_list[2] == {"_value_len": len(long)}


def test_post_tool_use_respects_audit_disabled(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    _install(project_dir)
    (project_dir / ".adjoint").mkdir()
    (project_dir / ".adjoint" / "config.toml").write_text(
        "[audit]\nenabled = false\n",
        encoding="utf-8",
    )
    stdin = json.dumps(
        {
            "session_id": "s",
            "cwd": str(project_dir),
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_response": {"exit_code": 0},
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
    assert _fetch_events() == []


def test_post_tool_use_audit_disabled_from_nested_cwd(
    adjoint_home: Path, project_dir: Path, run_hook_bin
) -> None:
    """``[audit] enabled = false`` at the repo root must work for subdir launches."""
    _install(project_dir)
    (project_dir / ".adjoint").mkdir()
    (project_dir / ".adjoint" / "config.toml").write_text(
        "[audit]\nenabled = false\n",
        encoding="utf-8",
    )
    nested = project_dir / "sub" / "deep"
    nested.mkdir(parents=True)
    stdin = json.dumps(
        {
            "session_id": "s",
            "cwd": str(nested),
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_response": {"exit_code": 0},
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
    assert _fetch_events() == [], "audit opt-out must apply to nested launches"


def test_post_tool_use_missing_db_is_noop(project_dir: Path, run_hook_bin) -> None:
    # No install → no events.db table. Hook must still exit 0.
    stdin = json.dumps(
        {
            "session_id": "s",
            "cwd": str(project_dir),
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {},
            "tool_response": {},
        }
    )
    cp = run_hook_bin("adjoint-hook-post-tool-use", stdin)
    assert cp.returncode == 0
