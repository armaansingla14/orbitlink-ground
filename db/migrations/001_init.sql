-- OrbitLink PostgreSQL schema, migration 001.
-- Applied automatically by the postgres container (docker-compose mounts this
-- directory into /docker-entrypoint-initdb.d).

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE stations (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    lat_deg     DOUBLE PRECISION NOT NULL CHECK (lat_deg BETWEEN -90 AND 90),
    lon_deg     DOUBLE PRECISION NOT NULL CHECK (lon_deg BETWEEN -180 AND 180),
    alt_m       DOUBLE PRECISION NOT NULL DEFAULT 0,
    min_el_deg  DOUBLE PRECISION NOT NULL DEFAULT 10
);

CREATE TABLE passes (
    id          BIGSERIAL PRIMARY KEY,
    station_id  INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    aos         TIMESTAMPTZ NOT NULL,
    los         TIMESTAMPTZ NOT NULL,
    tca         TIMESTAMPTZ NOT NULL,
    max_el_deg  DOUBLE PRECISION NOT NULL,
    duration_s  DOUBLE PRECISION NOT NULL,
    scheduled   BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (station_id, aos),
    CHECK (los > aos)
);
CREATE INDEX idx_passes_aos ON passes (aos);

CREATE TABLE telemetry_frames (
    id          BIGSERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL,
    scid        INTEGER NOT NULL,
    vcid        SMALLINT NOT NULL,
    frame_count SMALLINT NOT NULL,
    raw         BYTEA NOT NULL
);
CREATE INDEX idx_frames_received ON telemetry_frames (received_at);

CREATE TABLE telemetry_points (
    id          BIGSERIAL PRIMARY KEY,
    t           TIMESTAMPTZ NOT NULL,
    param       TEXT NOT NULL,
    value       DOUBLE PRECISION NOT NULL
);
CREATE INDEX idx_points_param_t ON telemetry_points (param, t DESC);

CREATE TABLE limits (
    param       TEXT PRIMARY KEY,
    red_low     DOUBLE PRECISION,
    yellow_low  DOUBLE PRECISION,
    yellow_high DOUBLE PRECISION,
    red_high    DOUBLE PRECISION,
    persistence INTEGER NOT NULL DEFAULT 1 CHECK (persistence >= 1),
    enabled     BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TYPE alert_severity AS ENUM ('info', 'warning', 'critical');

CREATE TABLE alerts (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL,
    severity     alert_severity NOT NULL,
    source       TEXT NOT NULL,
    message      TEXT NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    ack_at       TIMESTAMPTZ
);
CREATE INDEX idx_alerts_open ON alerts (created_at) WHERE NOT acknowledged;

CREATE TABLE commands (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL,
    name         TEXT NOT NULL,
    func         SMALLINT NOT NULL,
    args_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    args_hex     TEXT NOT NULL DEFAULT '',
    priority     SMALLINT NOT NULL DEFAULT 2 CHECK (priority BETWEEN 0 AND 3),
    state        TEXT NOT NULL CHECK (state IN ('queued', 'sent', 'acked', 'nacked', 'failed', 'expired')),
    seq          INTEGER CHECK (seq BETWEEN 0 AND 16383),
    attempts     INTEGER NOT NULL DEFAULT 0,
    sent_at      TIMESTAMPTZ,
    acked_at     TIMESTAMPTZ,
    ack_status   SMALLINT
);
CREATE INDEX idx_commands_state ON commands (state);

CREATE TABLE events (
    id          BIGSERIAL PRIMARY KEY,
    t           TIMESTAMPTZ NOT NULL,
    category    TEXT NOT NULL,
    name        TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_events_t ON events (t DESC);

CREATE TABLE timeline (
    id          BIGSERIAL PRIMARY KEY,
    start_t     TIMESTAMPTZ NOT NULL,
    end_t       TIMESTAMPTZ NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    ref_id      BIGINT,
    CHECK (end_t >= start_t)
);
CREATE INDEX idx_timeline_start ON timeline (start_t);

INSERT INTO schema_migrations (version) VALUES (1);

COMMIT;
