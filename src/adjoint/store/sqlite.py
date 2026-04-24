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

import contextlib
import sqlite3
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


def _bootstrap_schema(conn: sqlite3.Connection) -> None:
    """Create the ``schema_migrations`` bookkeeping table if it's missing."""
    conn.executescript(_BOOTSTRAP_SQL)


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    """Read-only: returns the set of migration names already recorded."""
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    return {r["name"] for r in rows}


def _discover_migrations() -> list[Path]:
    return sorted(migrations_dir().glob("*.sql"))


def _safe_rollback(conn: sqlite3.Connection) -> None:
    """Issue ROLLBACK iff a transaction is open; swallow OperationalError.

    ``c.execute('ROLLBACK')`` outside a transaction raises ``OperationalError``
    — we want to preserve the ORIGINAL exception the caller's ``try`` caught,
    not trade it for a noisier rollback error.
    """
    if not conn.in_transaction:
        return
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ROLLBACK")


def run_migrations(conn: sqlite3.Connection | None = None) -> list[str]:
    """Apply any migrations not yet recorded. Returns names of newly-applied ones.

    Migration DDL and the matching ``schema_migrations`` INSERT run inside a
    single ``executescript`` transaction so a crash between them can't leave
    the schema applied but unrecorded. Migration files must therefore NOT
    contain their own ``BEGIN``/``COMMIT`` — the wrapper supplies them.
    """
    owned = conn is None
    c = conn or connect()
    applied_now: list[str] = []
    try:
        _bootstrap_schema(c)
        already = _applied_migrations(c)
        for path in _discover_migrations():
            if path.name in already:
                continue
            sql = path.read_text(encoding="utf-8")
            # Embed the migration name via a safely quoted string literal
            # (migration file names are under our control and come from the
            # package tree, but quote-escaping is cheap insurance).
            safe_name = path.name.replace("'", "''")
            # nosec B608 — ``safe_name`` comes from ``path.name`` on a file we
            # ship inside the package tree, not user input. ``executescript``
            # does not accept bound parameters, which is why we string-build
            # the INSERT to keep it in the same atomic transaction as the DDL.
            combined = (
                "BEGIN;\n"
                f"{sql}\n"
                f"INSERT INTO schema_migrations(name) VALUES ('{safe_name}');\n"  # nosec B608
                "COMMIT;"
            )
            try:
                c.executescript(combined)
            except Exception:
                _safe_rollback(c)
                raise
            applied_now.append(path.name)
    finally:
        if owned:
            c.close()
    return applied_now
