"""SQLite connection + migration runner.

All adjoint processes (hooks, daemon, CLI) share a single on-disk database at
``~/.adjoint/events.db``. WAL mode means hooks can fire-and-forget single-row
inserts concurrently with long daemon transactions.

Migrations are numbered ``NNN_<name>.sql`` files in ``store/migrations/``.
``run_migrations()`` applies any that haven't been recorded in the
``schema_migrations`` bookkeeping table. It is safe to call repeatedly and is
invoked by ``adjoint install`` before hooks are wired into settings — there's
no runtime race between migration and first hook insert.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..paths import migrations_dir, user_paths

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  name       TEXT PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def connect() -> sqlite3.Connection:
    return _connect(user_paths().events_db)


@contextmanager
def transaction(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success, rolls back on exception."""
    owned = conn is None
    c = conn or connect()
    try:
        c.execute("BEGIN")
        yield c
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    finally:
        if owned:
            c.close()


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    conn.executescript(_BOOTSTRAP_SQL)
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    return {r["name"] for r in rows}


def _discover_migrations() -> list[Path]:
    return sorted(migrations_dir().glob("*.sql"))


def run_migrations(conn: sqlite3.Connection | None = None) -> list[str]:
    """Apply any migrations not yet recorded. Returns names of newly-applied ones."""
    owned = conn is None
    c = conn or connect()
    applied_now: list[str] = []
    try:
        already = _applied_migrations(c)
        for path in _discover_migrations():
            if path.name in already:
                continue
            sql = path.read_text(encoding="utf-8")
            c.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
            c.execute(
                "INSERT INTO schema_migrations(name) VALUES (?)",
                (path.name,),
            )
            applied_now.append(path.name)
    finally:
        if owned:
            c.close()
    return applied_now
