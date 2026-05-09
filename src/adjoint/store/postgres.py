"""Postgres-backed session store for adjoint.

Provides the same interface as ``sqlite.py`` (``connect()``, ``run_migrations()``)
but uses Postgres via psycopg2. Intended for multi-agent or team deployments
where multiple Claude Code instances share a single audit database.

## Why Postgres?

The SQLite store is correct for single-machine use. Postgres adds:

1. **Multi-agent concurrency**: WAL-mode SQLite handles concurrent readers but
   serialises all writers. Postgres handles many concurrent writers natively.
2. **LISTEN/NOTIFY**: agents can subscribe to audit events in real time —
   useful for live dashboards, cost monitoring, or triggering downstream jobs.
3. **Retention policies**: partitioned tables or TimescaleDB let you keep weeks
   of events without file-size concerns.
4. **Replication / backups**: standard Postgres tooling.

## Setup

1. Install psycopg2: ``pip install psycopg2-binary``
2. Create a database: ``createdb adjoint``
3. Set env var: ``ADJOINT_PG_DSN=postgresql://user@localhost/adjoint``
4. Run migrations: ``python -m adjoint store.postgres migrate``

## Drop-in usage

In your adjoint config (``~/.adjoint/config.toml``):

    [store]
    backend = "postgres"
    dsn = "postgresql://user@localhost/adjoint"

Then swap the import in any hook or CLI:

    # Before
    from adjoint.store.sqlite import connect

    # After
    from adjoint.store.postgres import connect  # same API

The connection object returned by ``connect()`` exposes the same cursor protocol
as the SQLite connection: ``.execute()``, ``.close()``, ``.commit()``, etc.
Psycopg2 cursors are dict-row compatible when you use ``RealDictCursor``.

## LISTEN/NOTIFY

After inserting a row into ``events``, the Postgres backend fires a NOTIFY on
the ``adjoint_events`` channel. Any subscriber (dashboard, monitor, test) can
react within milliseconds:

    import psycopg2, select
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("LISTEN adjoint_events")
    while True:
        select.select([conn], [], [], 5)
        conn.poll()
        for n in conn.notifies:
            print("event:", n.payload)

No deps beyond psycopg2. No Postgres extensions required.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PSYCOPG2_AVAILABLE = False

# ---------------------------------------------------------------------------
# Migration SQL
# ---------------------------------------------------------------------------

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

_SCHEMA_SQL = """
-- Audit events (mirrors SQLite events table)
CREATE TABLE IF NOT EXISTS events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT,
    event_type  TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS events_session_idx  ON events (session_id);
CREATE INDEX IF NOT EXISTS events_type_idx     ON events (event_type);
CREATE INDEX IF NOT EXISTS events_created_idx  ON events (created_at);

-- Trigger for LISTEN/NOTIFY after each insert
CREATE OR REPLACE FUNCTION _adjoint_notify_events()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify(
        'adjoint_events',
        json_build_object(
            'id',         NEW.id,
            'session_id', NEW.session_id,
            'event_type', NEW.event_type
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS adjoint_events_notify ON events;
CREATE TRIGGER adjoint_events_notify
AFTER INSERT ON events
FOR EACH ROW EXECUTE FUNCTION _adjoint_notify_events();
"""

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

_DEFAULT_DSN = "postgresql://localhost/adjoint"


def _get_dsn() -> str:
    return os.environ.get("ADJOINT_PG_DSN", _DEFAULT_DSN)


def connect(*, dsn: str | None = None, busy_timeout_ms: int = 5000) -> Any:
    """
    Open a Postgres connection with RealDictCursor as the default cursor factory.

    ``busy_timeout_ms`` is honoured via ``statement_timeout`` (Postgres equivalent
    of SQLite's busy_timeout). Latency-sensitive callers (hooks) should pass a
    tight value; long-running CLI/daemon callers leave it at the default.

    Returns a psycopg2 connection whose cursors behave like sqlite3.Row — columns
    are accessible by name.
    """
    if not _PSYCOPG2_AVAILABLE:
        raise ImportError(
            "psycopg2 is required for the Postgres store backend. "
            "Install it with: pip install psycopg2-binary"
        )
    resolved_dsn = dsn or _get_dsn()
    conn = psycopg2.connect(
        resolved_dsn,
        cursor_factory=psycopg2.extras.RealDictCursor,
        options=f"-c statement_timeout={busy_timeout_ms}ms",
    )
    conn.autocommit = True
    return conn


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def _applied_migrations(conn: Any) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM schema_migrations")
        return {r["name"] for r in cur.fetchall()}


def _discover_migrations() -> list[Path]:
    """Return SQL migration files from the store/migrations directory."""
    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        return []
    return sorted(migrations_dir.glob("*.sql"))


def run_migrations(*, dsn: str | None = None) -> list[str]:
    """
    Apply pending migrations to the Postgres database.

    Returns the list of migration names applied this call (empty if already up to date).
    Safe to call repeatedly — idempotent.

    Usage:
        python -m adjoint.store.postgres migrate
    or call programmatically before first use:
        from adjoint.store.postgres import run_migrations
        run_migrations()
    """
    conn = connect(dsn=dsn)
    applied: list[str] = []

    try:
        # Bootstrap: schema_migrations table
        with conn.cursor() as cur:
            cur.execute(_BOOTSTRAP_SQL)

        already = _applied_migrations(conn)

        # Built-in schema migration (always first)
        if "000_builtin_schema" not in already:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
                cur.execute(
                    "INSERT INTO schema_migrations (name) VALUES (%s) ON CONFLICT DO NOTHING",
                    ("000_builtin_schema",),
                )
            applied.append("000_builtin_schema")

        # File-based migrations (same pattern as sqlite.py)
        for path in _discover_migrations():
            name = path.stem
            if name in already:
                continue
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (name) VALUES (%s) ON CONFLICT DO NOTHING",
                    (name,),
                )
            applied.append(name)

    finally:
        conn.close()

    return applied


# ---------------------------------------------------------------------------
# High-level helpers (matches the call sites in sqlite.py)
# ---------------------------------------------------------------------------

def insert_event(
    conn: Any,
    *,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    """
    Insert one audit event row and return its auto-generated id.

    Fires LISTEN/NOTIFY automatically via the database trigger — callers do
    not need to do anything extra.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (session_id, event_type, payload_json)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (session_id, event_type, json.dumps(payload, default=str)),
        )
        return cur.fetchone()["id"]


def query_events(
    conn: Any,
    *,
    session_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Retrieve recent audit events. Optionally filter by session or type.
    Returns dicts with keys: id, session_id, event_type, payload_json, created_at.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if session_id is not None:
        clauses.append("session_id = %s")
        params.append(session_id)
    if event_type is not None:
        clauses.append("event_type = %s")
        params.append(event_type)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, session_id, event_type, payload_json, created_at "
            f"FROM events {where} ORDER BY id DESC LIMIT %s",
            params,
        )
        rows = cur.fetchall()

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["payload"] = json.loads(d["payload_json"])
        except (json.JSONDecodeError, KeyError):
            d["payload"] = {}
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    if len(_sys.argv) < 2 or _sys.argv[1] != "migrate":
        print("Usage: python -m adjoint.store.postgres migrate", file=_sys.stderr)
        _sys.exit(1)

    applied = run_migrations()
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("Database is up to date.")
