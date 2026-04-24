-- 001_initial.sql — initial schema
-- Four tables:
--   runs            operational state for detached runs (daemon-managed)
--   events          hook + run + provider event stream (audit + tracing)
--   compile_state   sha256-keyed incremental-compile dependency graph
--   knowledge_log   history of KB article mutations

CREATE TABLE IF NOT EXISTS runs (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  status      TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled','timeout')),
  command     TEXT NOT NULL,
  cwd         TEXT NOT NULL,
  provider    TEXT NOT NULL,
  exit_code   INTEGER,
  cost_usd    REAL,
  started_at  TIMESTAMP NOT NULL,
  ended_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT REFERENCES runs(id),
  session_id   TEXT,
  ts           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  event_type   TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS compile_state (
  daily_log_path   TEXT PRIMARY KEY,
  sha256           TEXT NOT NULL,
  last_compiled_at TIMESTAMP,
  cost_usd         REAL
);

CREATE TABLE IF NOT EXISTS knowledge_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  operation    TEXT NOT NULL,
  article_path TEXT NOT NULL,
  details_json TEXT
);
