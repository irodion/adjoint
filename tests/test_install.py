"""M0 install gate — verifies the plan's verification-step 1 end-to-end."""

from __future__ import annotations

import json
from pathlib import Path

from adjoint.install import apply_install, build_install_plan
from adjoint.paths import claude_settings_path

EXPECTED_EVENTS = {
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
}


def _settings(project_dir: Path) -> dict:
    return json.loads(claude_settings_path("project", project_dir).read_text(encoding="utf-8"))


def test_install_writes_all_six_hooks_and_mcp(adjoint_home: Path, project_dir: Path) -> None:
    plan, merged = build_install_plan("project", project_dir)
    apply_install(plan, merged)

    data = _settings(project_dir)
    assert set(data["hooks"].keys()) == EXPECTED_EVENTS
    for event, entries in data["hooks"].items():
        commands = [h["command"] for e in entries for h in e["hooks"]]
        assert any(c.startswith("adjoint-hook-") for c in commands), event

    assert data["mcpServers"]["adjoint"]["command"] == "adjoint-mcp"
    assert (adjoint_home / "events.db").is_file()


def test_reinstall_is_idempotent(adjoint_home: Path, project_dir: Path) -> None:
    plan, merged = build_install_plan("project", project_dir)
    apply_install(plan, merged)
    first = _settings(project_dir)

    plan2, merged2 = build_install_plan("project", project_dir)
    apply_install(plan2, merged2)
    second = _settings(project_dir)

    assert first == second
    assert plan2.hooks_added == []
    assert sorted(plan2.hooks_skipped) == sorted(EXPECTED_EVENTS)


def test_install_preserves_unrelated_user_entries(adjoint_home: Path, project_dir: Path) -> None:
    target = claude_settings_path("project", project_dir)
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
                "mcpServers": {"other": {"type": "stdio", "command": "other-mcp"}},
                "someUserKey": "preserved",
            }
        )
    )

    plan, merged = build_install_plan("project", project_dir)
    apply_install(plan, merged)

    data = _settings(project_dir)
    # Adjoint SessionStart added alongside user's existing entry.
    session_start_cmds = [h["command"] for e in data["hooks"]["SessionStart"] for h in e["hooks"]]
    assert "echo hi" in session_start_cmds
    assert "adjoint-hook-session-start" in session_start_cmds
    # Unrelated mcpServer preserved.
    assert data["mcpServers"]["other"]["command"] == "other-mcp"
    assert data["mcpServers"]["adjoint"]["command"] == "adjoint-mcp"
    # Unknown top-level keys preserved.
    assert data["someUserKey"] == "preserved"
    # Backup created because we mutated pre-existing content.
    assert plan.backup is not None and plan.backup.is_file()


def test_migrations_applied_and_recorded(adjoint_home: Path, project_dir: Path) -> None:
    plan, merged = build_install_plan("project", project_dir)
    result = apply_install(plan, merged)
    assert "001_initial.sql" in result.migrations_applied

    from adjoint.store.sqlite import connect

    with connect() as conn:  # type: ignore[attr-defined]
        pass  # connect doesn't use a CM; do it explicitly:

    conn = connect()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"runs", "events", "compile_state", "knowledge_log", "schema_migrations"} <= tables


def test_hooks_expected_from_status_helper(adjoint_home: Path, project_dir: Path) -> None:
    plan, merged = build_install_plan("project", project_dir)
    apply_install(plan, merged)
    from adjoint.cli import _hooks_installed

    found, total = _hooks_installed("project", project_dir)
    assert (found, total) == (6, 6)
