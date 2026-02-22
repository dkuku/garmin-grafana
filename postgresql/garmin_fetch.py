#!/usr/bin/env python3
"""Garmin Connect → PostgreSQL fetcher.

Sync loop:
  1. Check max(ts) from heart_rate_intraday (last local data)
  2. Check get_device_last_used() from Garmin API (last watch sync)
  3. If watch > local → fetch date range
  4. Sleep UPDATE_INTERVAL seconds

Bulk mode: set MANUAL_START_DATE + MANUAL_END_DATE → import range, then exit.
"""

import json
import logging
import signal
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from garminconnect import Garmin

import config
import fit_parser
import pg_writer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("garmin_fetch")

# All fetchable data types
ALL_TYPES = [
    "daily_stats", "heart_rate", "steps", "stress", "body_battery",
    "sleep", "breathing_rate", "hrv", "body_composition", "activities",
    "vo2_max", "race_predictions", "fitness_age", "training_status",
    "training_readiness", "hill_score", "endurance_score",
    "blood_pressure", "hydration",
]

_shutdown = False


def _signal_handler(signum, frame):
    global _shutdown
    log.info("Received signal %s, shutting down...", signum)
    _shutdown = True


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate() -> Garmin:
    """Authenticate to Garmin Connect, reusing saved tokens if possible."""
    token_dir = Path(config.GARMIN_TOKEN_DIR)
    token_dir.mkdir(parents=True, exist_ok=True)

    garmin = Garmin(
        email=config.GARMIN_EMAIL,
        password=config.GARMIN_PASSWORD,
    )

    try:
        garmin.login(str(token_dir))
        log.info("Logged in using saved tokens")
    except Exception:
        log.info("Token login failed, doing full login...")
        garmin.login()
        garmin.garth.dump(str(token_dir))
        log.info("Full login succeeded, tokens saved to %s", token_dir)

    return garmin


def re_authenticate(garmin: Garmin) -> Garmin:
    """Re-authenticate after auth error."""
    log.warning("Re-authenticating...")
    token_dir = Path(config.GARMIN_TOKEN_DIR)
    try:
        garmin.login()
        garmin.garth.dump(str(token_dir))
        log.info("Re-authentication succeeded")
        return garmin
    except Exception:
        log.error("Re-auth failed, creating fresh client")
        return authenticate()


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

_tz = ZoneInfo(config.USER_TIMEZONE)


def _today() -> date:
    return datetime.now(_tz).date()


def _date_range(start: date, end: date):
    """Yield dates from start to end inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _iso(d: date) -> str:
    return d.isoformat()


def _epoch_ms_to_dt(ms) -> datetime | None:
    """Convert Garmin epoch millis to tz-aware datetime, or None."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=_tz)


def _garmin_ts_to_dt(ts_val: str | int | None) -> datetime | None:
    """Parse Garmin timestamp (string or epoch millis) to tz-aware datetime."""
    if ts_val is None:
        return None
    if isinstance(ts_val, (int, float)):
        return _epoch_ms_to_dt(ts_val)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(ts_val, fmt)
            return naive.replace(tzinfo=_tz)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# API wrappers — safe calls with error classification
# ---------------------------------------------------------------------------

class RateLimited(Exception):
    pass


class AuthError(Exception):
    pass


def _safe_call(fn, *args, **kwargs):
    """Call Garmin API, raise typed exceptions for retryable errors."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        msg = str(e).lower()
        if "429" in msg or "too many" in msg or "rate" in msg:
            raise RateLimited(str(e)) from e
        if "401" in msg or "403" in msg or "auth" in msg or "login" in msg:
            raise AuthError(str(e)) from e
        if "500" in msg or "502" in msg or "503" in msg:
            log.warning("Server error from Garmin: %s", e)
            return None
        raise


# ---------------------------------------------------------------------------
# Data fetchers — one per data type, returns count of rows written
# ---------------------------------------------------------------------------

def fetch_daily_stats(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_stats, _iso(day))
    if not data:
        return 0
    row = {
        "ts": day,
        "total_steps": data.get("totalSteps"),
        "total_distance": data.get("totalDistanceMeters"),
        "active_calories": data.get("activeKilocalories"),
        "total_calories": data.get("totalKilocalories"),
        "bmr_calories": data.get("bmrKilocalories"),
        "floors_climbed": data.get("floorsAscended"),
        "floors_descended": data.get("floorsDescended"),
        "intensity_minutes": data.get("intensityMinutesGoal"),
        "moderate_intensity_minutes": data.get("moderateIntensityMinutes"),
        "vigorous_intensity_minutes": data.get("vigorousIntensityMinutes"),
        "avg_heart_rate": data.get("averageHeartRate"),
        "max_heart_rate": data.get("maxHeartRate"),
        "resting_heart_rate": data.get("restingHeartRate"),
        "min_heart_rate": data.get("minHeartRate"),
        "avg_stress": data.get("averageStressLevel"),
        "max_stress": data.get("maxStressLevel"),
        "stress_duration": data.get("stressDuration"),
        "rest_stress_duration": data.get("restStressDuration"),
        "activity_stress_duration": data.get("activityStressDuration"),
        "low_stress_duration": data.get("lowStressDuration"),
        "medium_stress_duration": data.get("mediumStressDuration"),
        "high_stress_duration": data.get("highStressDuration"),
        "avg_spo2": data.get("averageSpo2"),
        "lowest_spo2": data.get("lowestSpo2"),
        "avg_respiration": data.get("averageRespirationValue"),
        "lowest_respiration": data.get("lowestRespirationValue"),
        "highest_respiration": data.get("highestRespirationValue"),
        "body_battery_high": data.get("bodyBatteryHighestValue"),
        "body_battery_low": data.get("bodyBatteryLowestValue"),
        "body_battery_most_recent": data.get("bodyBatteryMostRecentValue"),
        "raw_json": json.dumps(data),
    }
    return pg_writer.upsert_daily_stats([row])


def fetch_heart_rate(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_heart_rates, _iso(day))
    if not data:
        return 0
    entries = data.get("heartRateValues") or []
    rows = []
    for ts_ms, hr in entries:
        if hr is not None and hr > 0:
            dt = _epoch_ms_to_dt(ts_ms)
            if dt:
                rows.append({"ts": dt, "heart_rate": hr})
    return pg_writer.upsert_heart_rate_intraday(rows)


def fetch_steps(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_steps_data, _iso(day))
    if not data:
        return 0
    rows = []
    for entry in data:
        ts_str = entry.get("startGMT") or entry.get("startTime")
        steps = entry.get("steps")
        if ts_str and steps is not None and steps > 0:
            dt = _garmin_ts_to_dt(ts_str)
            if dt:
                rows.append({"ts": dt, "steps": steps})
    return pg_writer.upsert_steps_intraday(rows)


def fetch_stress(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_all_day_stress, _iso(day))
    if not data:
        return 0
    entries = data.get("stressValuesArray") or data.get("bodyStressValuesList") or []
    rows = []
    for entry in entries:
        if isinstance(entry, list) and len(entry) >= 2:
            ts_ms, stress = entry[0], entry[1]
            if stress is not None and stress > 0:
                dt = _epoch_ms_to_dt(ts_ms)
                if dt:
                    rows.append({"ts": dt, "stress": stress})
    return pg_writer.upsert_stress_intraday(rows)


def fetch_body_battery(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_body_battery, _iso(day))
    if not data:
        return 0
    rows = []
    # API returns list of daily objects, each with bodyBatteryValuesArray
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict):
            # Extract intraday values from nested array [[epoch_ms, value], ...]
            values_arr = item.get("bodyBatteryValuesArray") or []
            for entry in values_arr:
                if isinstance(entry, list) and len(entry) >= 2:
                    ts_ms, bb = entry[0], entry[1]
                    if bb is not None and ts_ms is not None:
                        dt = _epoch_ms_to_dt(ts_ms)
                        if dt:
                            rows.append({"ts": dt, "body_battery": bb, "status": None})
            # If no intraday values, store daily summary
            if not values_arr:
                charged = item.get("charged")
                if charged is not None:
                    ts_str = item.get("startTimestampGMT") or item.get("date")
                    dt = _garmin_ts_to_dt(ts_str) if ts_str else None
                    if dt:
                        rows.append({"ts": dt, "body_battery": charged, "status": None})
        elif isinstance(item, list) and len(item) >= 2:
            ts_ms, bb = item[0], item[1]
            if bb is not None:
                dt = _epoch_ms_to_dt(ts_ms)
                if dt:
                    rows.append({"ts": dt, "body_battery": bb, "status": None})
    return pg_writer.upsert_body_battery_intraday(rows)


def fetch_sleep(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_sleep_data, _iso(day))
    if not data:
        return 0
    count = 0

    # Summary
    ds = data.get("dailySleepDTO") or data
    sleep_start = _garmin_ts_to_dt(ds.get("sleepStartTimestampGMT")) or _epoch_ms_to_dt(ds.get("sleepStartTimestampLocal"))
    sleep_end = _garmin_ts_to_dt(ds.get("sleepEndTimestampGMT")) or _epoch_ms_to_dt(ds.get("sleepEndTimestampLocal"))
    summary = {
        "ts": day,
        "sleep_start": sleep_start,
        "sleep_end": sleep_end,
        "total_sleep_seconds": ds.get("sleepTimeSeconds"),
        "deep_sleep_seconds": ds.get("deepSleepSeconds"),
        "light_sleep_seconds": ds.get("lightSleepSeconds"),
        "rem_sleep_seconds": ds.get("remSleepSeconds"),
        "awake_seconds": ds.get("awakeSleepSeconds"),
        "avg_spo2": ds.get("averageSpO2Value"),
        "lowest_spo2": ds.get("lowestSpO2Value"),
        "avg_respiration": ds.get("averageRespirationValue"),
        "avg_stress": ds.get("averageStressValue") or ds.get("sleepStressAverage"),
        "sleep_score": ds.get("sleepScores", {}).get("overall", {}).get("value") if isinstance(ds.get("sleepScores"), dict) else ds.get("sleepScoreValue"),
        "sleep_score_quality": ds.get("sleepScores", {}).get("qualityOfSleep", {}).get("qualifierKey") if isinstance(ds.get("sleepScores"), dict) else None,
        "sleep_score_recovery": ds.get("sleepScores", {}).get("recoveryScore", {}).get("value") if isinstance(ds.get("sleepScores"), dict) else None,
        "sleep_score_restfulness": ds.get("sleepScores", {}).get("restfulness", {}).get("value") if isinstance(ds.get("sleepScores"), dict) else None,
        "raw_json": json.dumps(data),
    }
    count += pg_writer.upsert_sleep_summary([summary])

    # Sleep levels (intraday)
    levels = data.get("sleepLevels") or []
    intraday_rows = []
    for lvl in levels:
        ts_str = lvl.get("startGMT")
        val = lvl.get("activityLevel")
        if ts_str and val is not None:
            dt = _garmin_ts_to_dt(ts_str)
            if dt:
                intraday_rows.append({"ts": dt, "metric": "sleep_level", "value": val})
    # SPO2 readings during sleep
    spo2_readings = data.get("spo2SleepSummary", {}).get("spo2ReadingSet") or []
    for r in spo2_readings:
        ts_str = r.get("readingTimestampGMT")
        val = r.get("reading")
        if ts_str and val is not None:
            dt = _garmin_ts_to_dt(ts_str)
            if dt:
                intraday_rows.append({"ts": dt, "metric": "spo2", "value": val})
    count += pg_writer.upsert_sleep_intraday(intraday_rows)
    return count


def fetch_breathing_rate(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_respiration_data, _iso(day))
    if not data:
        return 0
    entries = data.get("respirationValuesArray") or []
    rows = []
    for entry in entries:
        if isinstance(entry, list) and len(entry) >= 2:
            ts_ms, br = entry[0], entry[1]
            if br is not None and br > 0:
                dt = _epoch_ms_to_dt(ts_ms)
                if dt:
                    rows.append({"ts": dt, "breathing_rate": br})
    return pg_writer.upsert_breathing_rate_intraday(rows)


def fetch_hrv(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_hrv_data, _iso(day))
    if not data:
        return 0
    entries = data.get("hrvReadings") or data.get("hrvSummaries") or []
    rows = []
    for entry in entries:
        ts_str = entry.get("readingTimeGMT") or entry.get("startTimestampGMT") or entry.get("createTimeStamp")
        hrv = entry.get("hrvValue") or entry.get("weeklyAvg") or entry.get("rmssd")
        if ts_str and hrv is not None:
            dt = _garmin_ts_to_dt(ts_str)
            if dt:
                rows.append({"ts": dt, "hrv": hrv})
    return pg_writer.upsert_hrv_intraday(rows)


def fetch_body_composition(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_body_composition, _iso(day))
    if not data:
        return 0
    weigh_ins = data.get("dateWeightList") or data.get("weightList") or []
    if not weigh_ins and isinstance(data, dict):
        # Single reading
        weigh_ins = [data]
    rows = []
    for w in weigh_ins:
        ts_ms = w.get("date") or w.get("timestampGMT")
        dt = _epoch_ms_to_dt(ts_ms) if isinstance(ts_ms, (int, float)) else _garmin_ts_to_dt(str(ts_ms))
        if not dt:
            dt = datetime.combine(day, datetime.min.time()).replace(tzinfo=_tz)
        weight_g = w.get("weight")
        weight_kg = weight_g / 1000.0 if weight_g and weight_g > 500 else weight_g  # API returns grams
        rows.append({
            "ts": dt,
            "weight": weight_kg,
            "bmi": w.get("bmi"),
            "body_fat": w.get("bodyFat"),
            "body_water": w.get("bodyWater"),
            "bone_mass": (w.get("boneMass") or 0) / 1000.0 if w.get("boneMass") and w["boneMass"] > 500 else w.get("boneMass"),
            "muscle_mass": (w.get("muscleMass") or 0) / 1000.0 if w.get("muscleMass") and w["muscleMass"] > 500 else w.get("muscleMass"),
            "visceral_fat": w.get("visceralFat"),
            "metabolic_age": w.get("metabolicAge"),
            "raw_json": json.dumps(w),
        })
    return pg_writer.upsert_body_composition(rows)


def fetch_activities(garmin: Garmin, day: date) -> int:
    """Fetch activities for a given day."""
    activities = _safe_call(garmin.get_activities_by_date, _iso(day), _iso(day))
    if not activities:
        return 0

    known_ids = pg_writer.get_known_activity_ids(_iso(day))
    count = 0

    for act in activities:
        aid = act.get("activityId")
        if not aid:
            continue

        ts_str = act.get("startTimeGMT") or act.get("startTimeLocal")
        ts = _garmin_ts_to_dt(ts_str) if ts_str else datetime.combine(day, datetime.min.time()).replace(tzinfo=_tz)

        summary = {
            "activity_id": aid,
            "ts": ts,
            "activity_name": act.get("activityName"),
            "activity_type": act.get("activityType", {}).get("typeKey") if isinstance(act.get("activityType"), dict) else act.get("activityType"),
            "sport_type": act.get("sportTypeDTO", {}).get("typeKey") if isinstance(act.get("sportTypeDTO"), dict) else None,
            "distance": act.get("distance"),
            "duration": act.get("duration"),
            "elapsed_duration": act.get("elapsedDuration"),
            "moving_duration": act.get("movingDuration"),
            "elevation_gain": act.get("elevationGain"),
            "elevation_loss": act.get("elevationLoss"),
            "avg_heart_rate": act.get("averageHR"),
            "max_heart_rate": act.get("maxHR"),
            "avg_speed": act.get("averageSpeed"),
            "max_speed": act.get("maxSpeed"),
            "avg_cadence": act.get("averageRunningCadenceInStepsPerMinute") or act.get("averageBikingCadenceInRevPerMinute"),
            "max_cadence": act.get("maxRunningCadenceInStepsPerMinute") or act.get("maxBikingCadenceInRevPerMinute"),
            "avg_power": act.get("avgPower"),
            "max_power": act.get("maxPower"),
            "norm_power": act.get("normPower"),
            "calories": act.get("calories"),
            "avg_temperature": act.get("averageTemperature"),
            "training_effect_aerobic": act.get("aerobicTrainingEffect"),
            "training_effect_anaerobic": act.get("anaerobicTrainingEffect"),
            "vo2_max_activity": act.get("vO2MaxValue"),
            "has_gps": act.get("hasPolyline", False),
            "raw_json": json.dumps(act),
        }
        count += pg_writer.upsert_activity_summary([summary])

        # Fetch FIT data for GPS activities if not already fetched
        if aid not in known_ids and act.get("hasPolyline"):
            try:
                _fetch_activity_fit(garmin, aid)
            except Exception as e:
                log.warning("Failed to fetch FIT for activity %s: %s", aid, e)

    return count


def _fetch_activity_fit(garmin: Garmin, activity_id: int):
    """Download and parse FIT file for an activity."""
    from garminconnect import Garmin as GarminClass
    try:
        fmt = GarminClass.ActivityDownloadFormat.ORIGINAL
    except AttributeError:
        # Fallback if enum not available
        fmt = "fit"

    fit_data = _safe_call(garmin.download_activity, activity_id, dl_fmt=fmt)
    if not fit_data:
        return

    records, sessions, laps, lengths = fit_parser.parse_fit_bytes(fit_data, activity_id)

    if records:
        pg_writer.upsert_activity_gps(records)
        log.info("  Activity %s: %d GPS records", activity_id, len(records))
    if sessions:
        pg_writer.upsert_activity_session(sessions)
    if laps:
        pg_writer.upsert_activity_lap(laps)
    if lengths:
        pg_writer.upsert_activity_length(lengths)


def fetch_vo2_max(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_max_metrics, _iso(day))
    if not data:
        return 0
    generic = data.get("generic") or data
    vo2_run = None
    vo2_cycle = None
    if isinstance(generic, dict):
        vo2_run = generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxRunning")
        vo2_cycle = generic.get("vo2MaxCycling")
    elif isinstance(generic, list):
        for item in generic:
            sport = (item.get("sport") or "").lower()
            val = item.get("vo2MaxPreciseValue") or item.get("vo2MaxValue")
            if "run" in sport:
                vo2_run = val
            elif "cycl" in sport:
                vo2_cycle = val
    if vo2_run is None and vo2_cycle is None:
        return 0
    return pg_writer.upsert_vo2_max([{
        "ts": day,
        "vo2_max_running": vo2_run,
        "vo2_max_cycling": vo2_cycle,
        "raw_json": json.dumps(data),
    }])


def fetch_race_predictions(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_race_predictions)
    if not data:
        return 0
    # API may return list or dict
    predictions = data if isinstance(data, list) else [data]
    rows = []
    for pred in predictions:
        row = {
            "ts": day,
            "prediction_5k": pred.get("time5K"),
            "prediction_10k": pred.get("time10K"),
            "prediction_half": pred.get("timeHalfMarathon"),
            "prediction_marathon": pred.get("timeMarathon"),
            "raw_json": json.dumps(pred),
        }
        rows.append(row)
        break  # Take first only
    return pg_writer.upsert_race_predictions(rows)


def fetch_fitness_age(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_fitnessage_data, _iso(day))
    if not data:
        return 0
    return pg_writer.upsert_fitness_age([{
        "ts": day,
        "chronological_age": data.get("chronologicalAge"),
        "fitness_age": data.get("fitnessAge"),
        "raw_json": json.dumps(data),
    }])


def fetch_training_status(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_training_status, _iso(day))
    if not data:
        return 0
    return pg_writer.upsert_training_status([{
        "ts": day,
        "training_status": data.get("trainingStatusLabel") or data.get("latestTrainingStatusMessage"),
        "training_status_message": data.get("message") or data.get("trainingStatusMessage"),
        "weekly_load": data.get("weeklyTrainingLoad"),
        "optimal_load_low": data.get("optimalTrainingLoadLow"),
        "optimal_load_high": data.get("optimalTrainingLoadHigh"),
        "acute_load": data.get("acuteTrainingLoad"),
        "raw_json": json.dumps(data),
    }])


def fetch_training_readiness(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_training_readiness, _iso(day))
    if not data:
        return 0
    return pg_writer.upsert_training_readiness([{
        "ts": day,
        "readiness_score": data.get("score") or data.get("readinessScore"),
        "readiness_level": data.get("level") or data.get("readinessLevel"),
        "sleep_score": data.get("sleepScore"),
        "recovery_score": data.get("recoveryScore"),
        "hrv_status": data.get("hrvStatus"),
        "acute_load": data.get("acuteLoad"),
        "raw_json": json.dumps(data),
    }])


def fetch_hill_score(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_hill_score, _iso(day))
    if not data:
        return 0
    # May return list or dict
    entries = data if isinstance(data, list) else [data]
    rows = []
    for entry in entries:
        score = entry.get("hillScore") or entry.get("overallScore") or entry.get("score")
        if score is not None:
            rows.append({
                "ts": day,
                "score": score,
                "raw_json": json.dumps(entry),
            })
            break
    return pg_writer.upsert_hill_score(rows)


def fetch_endurance_score(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_endurance_score, _iso(day))
    if not data:
        return 0
    entries = data if isinstance(data, list) else [data]
    rows = []
    for entry in entries:
        score = entry.get("enduranceScore") or entry.get("overallScore") or entry.get("score")
        if score is not None:
            rows.append({
                "ts": day,
                "score": score,
                "raw_json": json.dumps(entry),
            })
            break
    return pg_writer.upsert_endurance_score(rows)


def fetch_blood_pressure(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_blood_pressure, _iso(day))
    if not data:
        return 0
    measurements = data.get("measurementSummaries") or (data if isinstance(data, list) else [data])
    rows = []
    for m in measurements:
        ts_ms = m.get("measurementTimestampGMT") or m.get("createTimestamp")
        dt = _epoch_ms_to_dt(ts_ms) if isinstance(ts_ms, (int, float)) else _garmin_ts_to_dt(str(ts_ms)) if ts_ms else None
        if not dt:
            dt = datetime.combine(day, datetime.min.time()).replace(tzinfo=_tz)
        rows.append({
            "ts": dt,
            "systolic": m.get("systolic"),
            "diastolic": m.get("diastolic"),
            "pulse": m.get("pulse"),
            "raw_json": json.dumps(m),
        })
    return pg_writer.upsert_blood_pressure(rows)


def fetch_hydration(garmin: Garmin, day: date) -> int:
    data = _safe_call(garmin.get_hydration_data, _iso(day))
    if not data:
        return 0
    return pg_writer.upsert_hydration([{
        "ts": day,
        "intake_ml": (data.get("intakeSummaryDTO", {}).get("valueInML") if isinstance(data.get("intakeSummaryDTO"), dict) else None) or data.get("valueInML"),
        "goal_ml": (data.get("goalSummaryDTO", {}).get("goalInML") if isinstance(data.get("goalSummaryDTO"), dict) else None) or data.get("goalInML"),
        "raw_json": json.dumps(data),
    }])


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

FETCHERS = {
    "daily_stats": fetch_daily_stats,
    "heart_rate": fetch_heart_rate,
    "steps": fetch_steps,
    "stress": fetch_stress,
    "body_battery": fetch_body_battery,
    "sleep": fetch_sleep,
    "breathing_rate": fetch_breathing_rate,
    "hrv": fetch_hrv,
    "body_composition": fetch_body_composition,
    "activities": fetch_activities,
    "vo2_max": fetch_vo2_max,
    "race_predictions": fetch_race_predictions,
    "fitness_age": fetch_fitness_age,
    "training_status": fetch_training_status,
    "training_readiness": fetch_training_readiness,
    "hill_score": fetch_hill_score,
    "endurance_score": fetch_endurance_score,
    "blood_pressure": fetch_blood_pressure,
    "hydration": fetch_hydration,
}


def _get_active_types() -> list[str]:
    if config.FETCH_SELECTION:
        selected = [s.strip() for s in config.FETCH_SELECTION.split(",")]
        return [t for t in selected if t in FETCHERS]
    return ALL_TYPES


def fetch_day(garmin: Garmin, day: date, types: list[str]) -> int:
    """Fetch all selected data types for a single day. Returns total rows."""
    total = 0
    for dtype in types:
        fn = FETCHERS.get(dtype)
        if not fn:
            continue
        try:
            n = fn(garmin, day)
            if n:
                total += n
                log.debug("  %s: %d rows", dtype, n)
        except RateLimited:
            raise
        except AuthError:
            raise
        except Exception as e:
            log.warning("  %s failed for %s: %s", dtype, day, e)
    return total


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def determine_fetch_range(garmin: Garmin) -> tuple[date, date] | None:
    """Determine which dates need fetching based on local vs Garmin state."""
    last_local = pg_writer.get_last_hr_timestamp()

    try:
        device_info = _safe_call(garmin.get_device_last_used)
    except Exception:
        device_info = None

    last_garmin_ts = None
    if device_info:
        ts_ms = device_info.get("lastUsedDeviceUploadTimestamp")
        if ts_ms:
            last_garmin_ts = _epoch_ms_to_dt(ts_ms)

    if last_garmin_ts:
        pg_writer.update_sync_status(last_garmin_sync_ts=last_garmin_ts)

    if last_local is None:
        # First run — fetch last 7 days
        start = _today() - timedelta(days=7)
        end = _today()
        log.info("First run, fetching last 7 days: %s → %s", start, end)
        return start, end

    local_date = last_local.date() if hasattr(last_local, "date") else last_local
    garmin_date = last_garmin_ts.date() if last_garmin_ts else _today()

    if garmin_date <= local_date:
        log.info("No new data (local: %s, garmin: %s)", local_date, garmin_date)
        return None

    start = local_date
    end = min(garmin_date, _today())
    log.info("New data available: %s → %s", start, end)
    return start, end


def run_bulk_import(garmin: Garmin):
    """Bulk import a date range, then exit."""
    start = date.fromisoformat(config.MANUAL_START_DATE)
    end = date.fromisoformat(config.MANUAL_END_DATE)
    types = _get_active_types()
    log.info("Bulk import: %s → %s, types: %s", start, end, types)

    for day in _date_range(start, end):
        if _shutdown:
            break
        try:
            n = fetch_day(garmin, day, types)
            log.info("Day %s: %d rows", day, n)
            pg_writer.update_sync_status(last_fetch_date=day, last_error=None)
        except RateLimited as e:
            log.warning("Rate limited at %s, waiting 30 min: %s", day, e)
            pg_writer.update_sync_status(last_error=f"Rate limited at {day}")
            time.sleep(1800)
            # Retry same day
            try:
                n = fetch_day(garmin, day, types)
                log.info("Day %s (retry): %d rows", day, n)
                pg_writer.update_sync_status(last_fetch_date=day, last_error=None)
            except Exception as e2:
                log.error("Retry failed for %s: %s", day, e2)
        except AuthError:
            garmin = re_authenticate(garmin)
            try:
                n = fetch_day(garmin, day, types)
                log.info("Day %s (re-auth): %d rows", day, n)
            except Exception as e2:
                log.error("Re-auth retry failed for %s: %s", day, e2)
        except Exception as e:
            log.error("Error on %s: %s", day, e)
            pg_writer.update_sync_status(last_error=f"{day}: {e}")

        # Small delay between days to avoid rate limiting
        time.sleep(1)

    log.info("Bulk import complete")


def run_sync_loop(garmin: Garmin):
    """Continuous sync loop."""
    types = _get_active_types()
    log.info("Sync loop started, interval=%ds, types=%s", config.UPDATE_INTERVAL, types)

    while not _shutdown:
        try:
            fetch_range = determine_fetch_range(garmin)
            if fetch_range:
                start, end = fetch_range
                for day in _date_range(start, end):
                    if _shutdown:
                        break
                    try:
                        n = fetch_day(garmin, day, types)
                        if n:
                            log.info("Day %s: %d rows", day, n)
                        pg_writer.update_sync_status(
                            last_sync_ts=datetime.now(_tz),
                            last_fetch_date=day,
                            last_error=None,
                        )
                    except RateLimited as e:
                        log.warning("Rate limited, waiting 30 min: %s", e)
                        pg_writer.update_sync_status(last_error=str(e))
                        time.sleep(1800)
                        break
                    except AuthError:
                        garmin = re_authenticate(garmin)
                        break
                    time.sleep(1)
            else:
                pg_writer.update_sync_status(
                    last_sync_ts=datetime.now(_tz),
                    last_error=None,
                )

        except RateLimited as e:
            log.warning("Rate limited in sync loop, waiting 30 min: %s", e)
            time.sleep(1800)
            continue
        except AuthError:
            garmin = re_authenticate(garmin)
            continue
        except Exception as e:
            log.error("Sync loop error: %s\n%s", e, traceback.format_exc())
            pg_writer.update_sync_status(last_error=str(e))

        # Sleep in small increments to allow graceful shutdown
        for _ in range(config.UPDATE_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("garmin-postgres starting")

    # Init DB schema
    pg_writer.init_schema()
    log.info("Database ready")

    # Authenticate
    garmin = authenticate()

    # Bulk or loop
    if config.MANUAL_START_DATE and config.MANUAL_END_DATE:
        run_bulk_import(garmin)
    else:
        run_sync_loop(garmin)

    pg_writer.close()
    log.info("garmin-postgres stopped")


if __name__ == "__main__":
    main()
