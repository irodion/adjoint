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
