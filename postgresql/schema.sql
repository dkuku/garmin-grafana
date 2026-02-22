-- garmin-postgres schema
-- All timestamps are UTC (timestamptz)

-- ============================================================
-- Daily aggregates
-- ============================================================

CREATE TABLE IF NOT EXISTS daily_stats (
    ts              DATE PRIMARY KEY,
    total_steps     INTEGER,
    total_distance  REAL,          -- meters
    active_calories REAL,
    total_calories  REAL,
    bmr_calories    REAL,
    floors_climbed  INTEGER,
    floors_descended INTEGER,
    intensity_minutes INTEGER,
    moderate_intensity_minutes INTEGER,
    vigorous_intensity_minutes INTEGER,
    avg_heart_rate  INTEGER,
    max_heart_rate  INTEGER,
    resting_heart_rate INTEGER,
    min_heart_rate  INTEGER,
    avg_stress      INTEGER,
    max_stress      INTEGER,
    stress_duration INTEGER,       -- seconds
    rest_stress_duration INTEGER,
    activity_stress_duration INTEGER,
    low_stress_duration INTEGER,
    medium_stress_duration INTEGER,
    high_stress_duration INTEGER,
    avg_spo2       REAL,
    lowest_spo2    REAL,
    avg_respiration REAL,
    lowest_respiration REAL,
    highest_respiration REAL,
    body_battery_high INTEGER,
    body_battery_low INTEGER,
    body_battery_most_recent INTEGER,
    raw_json       JSONB
);

-- ============================================================
-- Sleep
-- ============================================================

CREATE TABLE IF NOT EXISTS sleep_summary (
    ts                      DATE PRIMARY KEY,
    sleep_start             TIMESTAMPTZ,
    sleep_end               TIMESTAMPTZ,
    total_sleep_seconds     INTEGER,
    deep_sleep_seconds      INTEGER,
    light_sleep_seconds     INTEGER,
    rem_sleep_seconds       INTEGER,
    awake_seconds           INTEGER,
    avg_spo2               REAL,
    lowest_spo2            REAL,
    avg_respiration        REAL,
    avg_stress             REAL,
    sleep_score            INTEGER,
    sleep_score_quality    INTEGER,
    sleep_score_recovery   INTEGER,
    sleep_score_restfulness INTEGER,
    raw_json               JSONB
);

CREATE TABLE IF NOT EXISTS sleep_intraday (
    ts      TIMESTAMPTZ NOT NULL,
    metric  TEXT NOT NULL,          -- 'spo2', 'respiration', 'stress', 'movement', 'sleep_level'
    value   REAL,
    PRIMARY KEY (ts, metric)
);

-- ============================================================
-- Heart Rate
-- ============================================================

CREATE TABLE IF NOT EXISTS heart_rate_intraday (
    ts          TIMESTAMPTZ PRIMARY KEY,
    heart_rate  INTEGER NOT NULL
);

-- B-tree on timestamptz is sufficient for range queries; no expression index needed

-- ============================================================
-- Steps
-- ============================================================

CREATE TABLE IF NOT EXISTS steps_intraday (
    ts      TIMESTAMPTZ PRIMARY KEY,
    steps   INTEGER NOT NULL
);

-- ============================================================
-- Stress
-- ============================================================

CREATE TABLE IF NOT EXISTS stress_intraday (
    ts      TIMESTAMPTZ PRIMARY KEY,
    stress  INTEGER NOT NULL
);

-- ============================================================
-- Body Battery
-- ============================================================

CREATE TABLE IF NOT EXISTS body_battery_intraday (
    ts             TIMESTAMPTZ PRIMARY KEY,
    body_battery   INTEGER NOT NULL,
    status         TEXT           -- 'CHARGING', 'DRAINING', etc.
);

-- ============================================================
-- Breathing Rate
-- ============================================================

CREATE TABLE IF NOT EXISTS breathing_rate_intraday (
    ts               TIMESTAMPTZ PRIMARY KEY,
    breathing_rate   REAL NOT NULL
);

-- ============================================================
-- HRV (Heart Rate Variability)
-- ============================================================

CREATE TABLE IF NOT EXISTS hrv_intraday (
    ts      TIMESTAMPTZ PRIMARY KEY,
    hrv     REAL NOT NULL          -- RMSSD in ms
);

-- ============================================================
-- Body Composition
-- ============================================================

CREATE TABLE IF NOT EXISTS body_composition (
    ts                  TIMESTAMPTZ PRIMARY KEY,
    weight              REAL,      -- kg
    bmi                 REAL,
    body_fat            REAL,      -- %
    body_water          REAL,      -- %
    bone_mass           REAL,      -- kg
    muscle_mass         REAL,      -- kg
    visceral_fat        REAL,
    metabolic_age       INTEGER,
    raw_json            JSONB
);

-- ============================================================
-- Activities
-- ============================================================

CREATE TABLE IF NOT EXISTS activity_summary (
    activity_id         BIGINT PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL,
    activity_name       TEXT,
    activity_type       TEXT,
    sport_type          TEXT,
    distance            REAL,          -- meters
    duration            REAL,          -- seconds
    elapsed_duration    REAL,
    moving_duration     REAL,
    elevation_gain      REAL,
    elevation_loss      REAL,
    avg_heart_rate      INTEGER,
    max_heart_rate      INTEGER,
    avg_speed           REAL,          -- m/s
    max_speed           REAL,
    avg_cadence         REAL,
    max_cadence         REAL,
    avg_power           REAL,          -- watts
    max_power           REAL,
    norm_power          REAL,
    calories            REAL,
    avg_temperature     REAL,
    training_effect_aerobic  REAL,
    training_effect_anaerobic REAL,
    vo2_max_activity    REAL,
    has_gps             BOOLEAN DEFAULT FALSE,
    raw_json            JSONB
);

CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_summary (ts);
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity_summary (activity_type);

CREATE TABLE IF NOT EXISTS activity_gps (
    activity_id     BIGINT NOT NULL REFERENCES activity_summary(activity_id) ON DELETE CASCADE,
    ts              TIMESTAMPTZ NOT NULL,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    altitude        REAL,
    heart_rate      INTEGER,
    cadence         INTEGER,
    speed           REAL,
    power           INTEGER,
    temperature     REAL,
    distance        REAL,            -- cumulative meters
    PRIMARY KEY (activity_id, ts)
);

CREATE TABLE IF NOT EXISTS activity_session (
    activity_id     BIGINT NOT NULL REFERENCES activity_summary(activity_id) ON DELETE CASCADE,
    session_index   INTEGER NOT NULL,
    sport           TEXT,
    sub_sport       TEXT,
    start_time      TIMESTAMPTZ,
    total_elapsed_time REAL,
    total_timer_time REAL,
    total_distance  REAL,
    total_calories  INTEGER,
    avg_heart_rate  INTEGER,
    max_heart_rate  INTEGER,
    avg_speed       REAL,
    max_speed       REAL,
    avg_cadence     REAL,
    max_cadence     REAL,
    avg_power       INTEGER,
    max_power       INTEGER,
    PRIMARY KEY (activity_id, session_index)
);

CREATE TABLE IF NOT EXISTS activity_lap (
    activity_id     BIGINT NOT NULL REFERENCES activity_summary(activity_id) ON DELETE CASCADE,
    lap_index       INTEGER NOT NULL,
    start_time      TIMESTAMPTZ,
    total_elapsed_time REAL,
    total_timer_time REAL,
    total_distance  REAL,
    total_calories  INTEGER,
    avg_heart_rate  INTEGER,
    max_heart_rate  INTEGER,
    avg_speed       REAL,
    max_speed       REAL,
    avg_cadence     REAL,
    max_cadence     REAL,
    avg_power       INTEGER,
    max_power       INTEGER,
    PRIMARY KEY (activity_id, lap_index)
);

CREATE TABLE IF NOT EXISTS activity_length (
    activity_id     BIGINT NOT NULL REFERENCES activity_summary(activity_id) ON DELETE CASCADE,
    length_index    INTEGER NOT NULL,
    start_time      TIMESTAMPTZ,
    total_elapsed_time REAL,
    total_timer_time REAL,
    total_strokes   INTEGER,
    avg_speed       REAL,
    avg_cadence     REAL,
    swim_stroke     TEXT,
    length_type     TEXT,
    PRIMARY KEY (activity_id, length_index)
);

-- ============================================================
-- Performance / Training Metrics
-- ============================================================

CREATE TABLE IF NOT EXISTS vo2_max (
    ts          DATE PRIMARY KEY,
    vo2_max_running  REAL,
    vo2_max_cycling  REAL,
    raw_json    JSONB
);

CREATE TABLE IF NOT EXISTS race_predictions (
    ts          DATE PRIMARY KEY,
    prediction_5k    REAL,       -- seconds
    prediction_10k   REAL,
    prediction_half  REAL,
    prediction_marathon REAL,
    raw_json    JSONB
);

CREATE TABLE IF NOT EXISTS fitness_age (
    ts              DATE PRIMARY KEY,
    chronological_age INTEGER,
    fitness_age     REAL,
    raw_json        JSONB
);

CREATE TABLE IF NOT EXISTS training_status (
    ts                  DATE PRIMARY KEY,
    training_status     TEXT,
    training_status_message TEXT,
    weekly_load         REAL,
    optimal_load_low    REAL,
    optimal_load_high   REAL,
    acute_load          REAL,
    raw_json            JSONB
);

CREATE TABLE IF NOT EXISTS training_readiness (
    ts              DATE PRIMARY KEY,
    readiness_score INTEGER,
    readiness_level TEXT,
    sleep_score     INTEGER,
    recovery_score  INTEGER,
    hrv_status      TEXT,
    acute_load      REAL,
    raw_json        JSONB
);

CREATE TABLE IF NOT EXISTS hill_score (
    ts          DATE PRIMARY KEY,
    score       REAL,
    raw_json    JSONB
);

CREATE TABLE IF NOT EXISTS endurance_score (
    ts          DATE PRIMARY KEY,
    score       REAL,
    raw_json    JSONB
);

CREATE TABLE IF NOT EXISTS lactate_threshold (
    ts              DATE NOT NULL,
    sport           TEXT NOT NULL DEFAULT 'running',
    heart_rate      INTEGER,
    speed           REAL,           -- m/s
    raw_json        JSONB,
    PRIMARY KEY (ts, sport)
);

-- ============================================================
-- Health Measurements
-- ============================================================

CREATE TABLE IF NOT EXISTS blood_pressure (
    ts              TIMESTAMPTZ PRIMARY KEY,
    systolic        INTEGER,
    diastolic       INTEGER,
    pulse           INTEGER,
    raw_json        JSONB
);

CREATE TABLE IF NOT EXISTS hydration (
    ts              DATE PRIMARY KEY,
    intake_ml       INTEGER,
    goal_ml         INTEGER,
    raw_json        JSONB
);

-- ============================================================
-- Sync tracking
-- ============================================================

CREATE TABLE IF NOT EXISTS sync_status (
    id                  INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_sync_ts        TIMESTAMPTZ,
    last_garmin_sync_ts TIMESTAMPTZ,
    last_fetch_date     DATE,
    last_error          TEXT,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO sync_status (id) VALUES (1) ON CONFLICT DO NOTHING;

-- ============================================================
-- Grants for Grafana read-only user
-- ============================================================

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_garmin') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA public TO grafana_garmin';
        EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_garmin';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_garmin';
    END IF;
END
$$;
