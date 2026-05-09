"""Tests for adjoint/store/postgres.py.

Uses unittest.mock to avoid needing a real Postgres instance in CI.
Tests the connection helper, migration runner, insert_event, and query_events.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call as mock_call

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# We mock psycopg2 globally so no DB is needed
psycopg2_mock = MagicMock()
psycopg2_mock.extras = MagicMock()
psycopg2_mock.extras.RealDictCursor = MagicMock()

import sys as _sys
_sys.modules.setdefault("psycopg2", psycopg2_mock)
_sys.modules.setdefault("psycopg2.extras", psycopg2_mock.extras)

from adjoint.store import postgres


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class TestConnect:
    def test_connect_returns_connection(self):
        fake_conn = MagicMock()
        with patch("adjoint.store.postgres.psycopg2") as pg:
            pg.connect.return_value = fake_conn
            pg.extras.RealDictCursor = MagicMock()
            conn = postgres.connect(dsn="postgresql://localhost/test")
        assert conn is fake_conn

    def test_autocommit_set(self):
        fake_conn = MagicMock()
        with patch("adjoint.store.postgres.psycopg2") as pg:
            pg.connect.return_value = fake_conn
            pg.extras.RealDictCursor = MagicMock()
            postgres.connect(dsn="postgresql://localhost/test")
        assert fake_conn.autocommit is True

    def test_no_psycopg2_raises_import_error(self):
        with patch.object(postgres, "_PSYCOPG2_AVAILABLE", False):
            try:
                postgres.connect()
                assert False, "Should have raised ImportError"
            except ImportError as e:
                assert "psycopg2" in str(e)


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

class TestRunMigrations:
    def _make_conn(self) -> MagicMock:
        """Build a mock connection with a cursor that supports fetchall."""
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.return_value = []  # no migrations applied yet
        conn.cursor.return_value = cursor
        return conn

    def test_applies_builtin_schema_on_empty_db(self):
        conn = self._make_conn()
        with patch.object(postgres, "connect", return_value=conn):
            applied = postgres.run_migrations()
        assert "000_builtin_schema" in applied

    def test_skips_already_applied(self):
        conn = self._make_conn()
        # Pretend 000_builtin_schema is already applied
        conn.cursor.return_value.fetchall.return_value = [
            {"name": "000_builtin_schema"}
        ]
        with patch.object(postgres, "connect", return_value=conn):
            applied = postgres.run_migrations()
        assert "000_builtin_schema" not in applied

    def test_closes_connection_on_success(self):
        conn = self._make_conn()
        with patch.object(postgres, "connect", return_value=conn):
            postgres.run_migrations()
        conn.close.assert_called_once()

    def test_closes_connection_on_error(self):
        conn = self._make_conn()
        conn.cursor.return_value.execute.side_effect = Exception("DB error")
        with patch.object(postgres, "connect", return_value=conn):
            try:
                postgres.run_migrations()
            except Exception:
                pass
        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# insert_event
# ---------------------------------------------------------------------------

class TestInsertEvent:
    def test_inserts_and_returns_id(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = {"id": 42}
        conn.cursor.return_value = cursor

        result = postgres.insert_event(
            conn,
            session_id="sess-abc",
            event_type="hook.PostToolUse",
            payload={"tool_name": "Read"},
        )
        assert result == 42

    def test_payload_serialized_as_json(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = {"id": 1}
        conn.cursor.return_value = cursor

        payload = {"tool_name": "Write", "file_path": "/src/main.py"}
        postgres.insert_event(
            conn,
            session_id="s",
            event_type="hook.PostToolUse",
            payload=payload,
        )

        # Check that the SQL call included the JSON-serialized payload
        execute_args = cursor.execute.call_args[0]
        assert len(execute_args) == 2
        _sql, params = execute_args
        assert params[0] == "s"
        assert "PostToolUse" in params[1]
        parsed = json.loads(params[2])
        assert parsed["tool_name"] == "Write"


# ---------------------------------------------------------------------------
# query_events
# ---------------------------------------------------------------------------

class TestQueryEvents:
    def _make_conn_with_rows(self, rows: list[dict]) -> MagicMock:
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = lambda s: cursor
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.fetchall.return_value = rows
        conn.cursor.return_value = cursor
        return conn

    def test_returns_list(self):
        conn = self._make_conn_with_rows([])
        result = postgres.query_events(conn)
        assert isinstance(result, list)

    def test_parses_payload_json(self):
        rows = [{
            "id": 1,
            "session_id": "abc",
            "event_type": "hook.PostToolUse",
            "payload_json": '{"tool_name": "Read"}',
            "created_at": "2026-01-01T00:00:00",
        }]
        conn = self._make_conn_with_rows(rows)
        result = postgres.query_events(conn)
        assert result[0]["payload"]["tool_name"] == "Read"

    def test_handles_invalid_payload_json(self):
        rows = [{
            "id": 1,
            "session_id": "abc",
            "event_type": "hook.PostToolUse",
            "payload_json": "not-json",
            "created_at": "2026-01-01T00:00:00",
        }]
        conn = self._make_conn_with_rows(rows)
        result = postgres.query_events(conn)
        assert result[0]["payload"] == {}

    def test_session_id_filter_in_query(self):
        conn = self._make_conn_with_rows([])
        postgres.query_events(conn, session_id="mysession")
        # Verify the WHERE clause was included
        execute_args = conn.cursor.return_value.execute.call_args[0]
        assert "session_id" in execute_args[0]
        assert "mysession" in execute_args[1]

    def test_event_type_filter_in_query(self):
        conn = self._make_conn_with_rows([])
        postgres.query_events(conn, event_type="hook.PostToolUse")
        execute_args = conn.cursor.return_value.execute.call_args[0]
        assert "event_type" in execute_args[0]
        assert "hook.PostToolUse" in execute_args[1]
