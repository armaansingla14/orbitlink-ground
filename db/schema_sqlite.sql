-- OrbitLink schema, SQLite dialect (used for tests and local runs).
-- The PostgreSQL equivalent lives in db/migrations/001_init.sql.

CREATE TABLE IF NOT EXISTS stations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    lat_deg     REAL NOT NULL,
    lon_deg     REAL NOT NULL,
    alt_m       REAL NOT NULL DEFAULT 0,
    min_el_deg  REAL NOT NULL DEFAULT 10
);

CREATE TABLE IF NOT EXISTS passes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id  INTEGER NOT NULL REFERENCES stations(id),
    aos         TEXT NOT NULL,
    los         TEXT NOT NULL,
    tca         TEXT NOT NULL,
    max_el_deg  REAL NOT NULL,
    duration_s  REAL NOT NULL,
    scheduled   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (station_id, aos)
);
CREATE INDEX IF NOT EXISTS idx_passes_aos ON passes(aos);

CREATE TABLE IF NOT EXISTS telemetry_frames (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    scid        INTEGER NOT NULL,
    vcid        INTEGER NOT NULL,
    frame_count INTEGER NOT NULL,
    raw         BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    t           TEXT NOT NULL,
    param       TEXT NOT NULL,
    value       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_points_param_t ON telemetry_points(param, t);

CREATE TABLE IF NOT EXISTS limits (
    param       TEXT PRIMARY KEY,
    red_low     REAL,
    yellow_low  REAL,
    yellow_high REAL,
    red_high    REAL,
    persistence INTEGER NOT NULL DEFAULT 1,
    enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    source       TEXT NOT NULL,
    message      TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    ack_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(acknowledged, created_at);

CREATE TABLE IF NOT EXISTS commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    name         TEXT NOT NULL,
    func         INTEGER NOT NULL,
    args_json    TEXT NOT NULL DEFAULT '{}',
    args_hex     TEXT NOT NULL DEFAULT '',
    priority     INTEGER NOT NULL DEFAULT 2,
    state        TEXT NOT NULL,
    seq          INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    sent_at      TEXT,
    acked_at     TEXT,
    ack_status   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commands_state ON commands(state);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    t           TEXT NOT NULL,
    category    TEXT NOT NULL,
    name        TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_t ON events(t);

CREATE TABLE IF NOT EXISTS timeline (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    start_t     TEXT NOT NULL,
    end_t       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    ref_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_timeline_start ON timeline(start_t);
