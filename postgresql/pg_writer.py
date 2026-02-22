"""PostgreSQL writer with connection pool and per-table upsert methods."""

import logging
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import config

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=config.PG_DSN,
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )
    return _pool


def init_schema():
    """Run schema.sql to create/update tables."""
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with get_pool().connection() as conn:
        conn.execute(sql)
        conn.commit()
    log.info("Schema initialized")


def close():
    global _pool
    if _pool:
        _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Generic upsert helper
# ---------------------------------------------------------------------------

def _upsert(table: str, rows: list[dict], conflict_cols: list[str], *, conn=None):
    """Generic upsert: INSERT ... ON CONFLICT (conflict_cols) DO UPDATE SET ..."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    update_cols = [c for c in cols if c not in conflict_cols]
    col_list = ", ".join(cols)
    placeholders = ", ".join(f"%({c})s" for c in cols)
    conflict = ", ".join(conflict_cols)
    if update_cols:
        set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {set_clause}"
        )
    else:
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) DO NOTHING"
        )

    def _exec(c):
        with c.cursor() as cur:
            cur.executemany(sql, rows)
        return len(rows)

    if conn:
        return _exec(conn)
    with get_pool().connection() as c:
        n = _exec(c)
        c.commit()
        return n


# ---------------------------------------------------------------------------
# Per-table writers
# ---------------------------------------------------------------------------

def upsert_daily_stats(rows: list[dict]):
    return _upsert("daily_stats", rows, ["ts"])


def upsert_sleep_summary(rows: list[dict]):
    return _upsert("sleep_summary", rows, ["ts"])


def upsert_sleep_intraday(rows: list[dict]):
    return _upsert("sleep_intraday", rows, ["ts", "metric"])


def upsert_heart_rate_intraday(rows: list[dict]):
    return _upsert("heart_rate_intraday", rows, ["ts"])


def upsert_steps_intraday(rows: list[dict]):
    return _upsert("steps_intraday", rows, ["ts"])


def upsert_stress_intraday(rows: list[dict]):
    return _upsert("stress_intraday", rows, ["ts"])


def upsert_body_battery_intraday(rows: list[dict]):
    return _upsert("body_battery_intraday", rows, ["ts"])


def upsert_breathing_rate_intraday(rows: list[dict]):
    return _upsert("breathing_rate_intraday", rows, ["ts"])


def upsert_hrv_intraday(rows: list[dict]):
    return _upsert("hrv_intraday", rows, ["ts"])


def upsert_body_composition(rows: list[dict]):
    return _upsert("body_composition", rows, ["ts"])


def upsert_activity_summary(rows: list[dict]):
    return _upsert("activity_summary", rows, ["activity_id"])


def upsert_activity_gps(rows: list[dict]):
    return _upsert("activity_gps", rows, ["activity_id", "ts"])


def upsert_activity_session(rows: list[dict]):
    return _upsert("activity_session", rows, ["activity_id", "session_index"])


def upsert_activity_lap(rows: list[dict]):
    return _upsert("activity_lap", rows, ["activity_id", "lap_index"])


def upsert_activity_length(rows: list[dict]):
    return _upsert("activity_length", rows, ["activity_id", "length_index"])


def upsert_vo2_max(rows: list[dict]):
    return _upsert("vo2_max", rows, ["ts"])


def upsert_race_predictions(rows: list[dict]):
    return _upsert("race_predictions", rows, ["ts"])


def upsert_fitness_age(rows: list[dict]):
    return _upsert("fitness_age", rows, ["ts"])


def upsert_training_status(rows: list[dict]):
    return _upsert("training_status", rows, ["ts"])


def upsert_training_readiness(rows: list[dict]):
    return _upsert("training_readiness", rows, ["ts"])


def upsert_hill_score(rows: list[dict]):
    return _upsert("hill_score", rows, ["ts"])


def upsert_endurance_score(rows: list[dict]):
    return _upsert("endurance_score", rows, ["ts"])


def upsert_lactate_threshold(rows: list[dict]):
    return _upsert("lactate_threshold", rows, ["ts", "sport"])


def upsert_blood_pressure(rows: list[dict]):
    return _upsert("blood_pressure", rows, ["ts"])


def upsert_hydration(rows: list[dict]):
    return _upsert("hydration", rows, ["ts"])


def update_sync_status(*, last_sync_ts=None, last_garmin_sync_ts=None,
                        last_fetch_date=None, last_error=None):
    sets = ["updated_at = NOW()"]
    params = {}
    if last_sync_ts is not None:
        sets.append("last_sync_ts = %(last_sync_ts)s")
        params["last_sync_ts"] = last_sync_ts
    if last_garmin_sync_ts is not None:
        sets.append("last_garmin_sync_ts = %(last_garmin_sync_ts)s")
        params["last_garmin_sync_ts"] = last_garmin_sync_ts
    if last_fetch_date is not None:
        sets.append("last_fetch_date = %(last_fetch_date)s")
        params["last_fetch_date"] = last_fetch_date
    if last_error is not None:
        sets.append("last_error = %(last_error)s")
        params["last_error"] = last_error
    sql = f"UPDATE sync_status SET {', '.join(sets)} WHERE id = 1"
    with get_pool().connection() as conn:
        conn.execute(sql, params)
        conn.commit()


def get_last_hr_timestamp():
    """Return the max timestamp from heart_rate_intraday, or None."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT MAX(ts) AS max_ts FROM heart_rate_intraday"
        ).fetchone()
        return row["max_ts"] if row else None


def get_known_activity_ids(since_date: str) -> set[int]:
    """Return set of activity_ids already in DB since given date."""
    with get_pool().connection() as conn:
        rows = conn.execute(
            "SELECT activity_id FROM activity_summary WHERE ts >= %s",
            (since_date,),
        ).fetchall()
        return {r["activity_id"] for r in rows}
