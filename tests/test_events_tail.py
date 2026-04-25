"""Tests for the ``adjoint events tail`` CLI."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner


def _install(project_dir: Path) -> None:
    from adjoint.install import apply_install, build_install_plan

    plan, merged = build_install_plan("project", project_dir)
    apply_install(plan, merged)


def _seed(events: list[tuple[str, str, str]]) -> None:
    """Insert (session_id, event_type, payload_json) rows."""
    from adjoint.store.sqlite import connect

    conn = connect()
    try:
        conn.executemany(
            "INSERT INTO events(session_id, event_type, payload_json) VALUES(?, ?, ?)",
            events,
        )
    finally:
        conn.close()


def test_tail_shows_last_n(adjoint_home: Path, project_dir: Path) -> None:
    _install(project_dir)
    _seed(
        [
            ("s1", "hook.PreToolUse", '{"tool_name":"Bash"}'),
            ("s1", "hook.PostToolUse", '{"tool_name":"Bash"}'),
            ("s2", "hook.UserPromptSubmit", '{"len":42}'),
        ]
    )

    from adjoint.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["events", "tail", "-n", "2"])
    assert result.exit_code == 0, result.output
    # Most recent two; rendered in chronological order (oldest first).
    assert "hook.PostToolUse" in result.output
    assert "hook.UserPromptSubmit" in result.output
    assert "hook.PreToolUse" not in result.output


def test_tail_type_filter_exact(adjoint_home: Path, project_dir: Path) -> None:
    _install(project_dir)
    _seed(
        [
            ("s", "hook.PreToolUse", "{}"),
            ("s", "hook.PostToolUse", "{}"),
            ("s", "memory.flush.ok", "{}"),
        ]
    )
    from adjoint.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["events", "tail", "--type", "hook.PostToolUse"])
    assert result.exit_code == 0, result.output
    assert "hook.PostToolUse" in result.output
    assert "hook.PreToolUse" not in result.output
    assert "memory.flush.ok" not in result.output


def test_tail_type_prefix_filter(adjoint_home: Path, project_dir: Path) -> None:
    _install(project_dir)
    _seed(
        [
            ("s", "hook.PreToolUse", "{}"),
            ("s", "hook.PostToolUse", "{}"),
            ("s", "memory.flush.ok", "{}"),
        ]
    )
    from adjoint.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["events", "tail", "--type", "hook."])
    assert result.exit_code == 0, result.output
    assert "hook.PreToolUse" in result.output
    assert "hook.PostToolUse" in result.output
    assert "memory.flush.ok" not in result.output


def test_tail_empty_table(adjoint_home: Path, project_dir: Path) -> None:
    _install(project_dir)
    from adjoint.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["events", "tail", "-n", "5"])
    assert result.exit_code == 0, result.output
    assert "no events" in result.output


def test_tail_errors_when_db_missing(adjoint_home: Path) -> None:
    # No install → no events.db; CLI should exit 1 with a friendly hint.
    from adjoint.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["events", "tail"])
    assert result.exit_code == 1
    assert "adjoint install" in result.output


def test_tail_errors_when_events_table_missing(adjoint_home: Path) -> None:
    """File present but no migrations run → friendly hint, not OperationalError.

    Reproduces the partial-install path where ``adjoint-hook-post-tool-use``
    created ``events.db`` via ``sqlite3.connect`` but the schema was never
    applied.
    """
    from adjoint.cli import app
    from adjoint.store.sqlite import connect

    # Create the file with no tables (mirrors what connect() does on first
    # call before run_migrations).
    conn = connect()
    conn.close()
    assert (adjoint_home / "events.db").is_file()

    runner = CliRunner()
    result = runner.invoke(app, ["events", "tail"])
    assert result.exit_code == 1
    assert "events table missing" in result.output
    assert "adjoint install" in result.output
