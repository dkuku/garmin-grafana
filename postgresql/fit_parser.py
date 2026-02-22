"""Parse Garmin FIT files for GPS records, sessions, laps, and lengths."""

import io
import logging
import zipfile
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# FIT semicircles to degrees conversion
SEMICIRCLE_TO_DEG = 180.0 / (2**31)


def _sc_to_deg(semicircles) -> float | None:
    """Convert FIT semicircles to decimal degrees."""
    if semicircles is None:
        return None
    return semicircles * SEMICIRCLE_TO_DEG


def _fit_ts(ts) -> datetime | None:
    """Convert fitparse timestamp to UTC datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    return None


def _get_field(record, name, fallback=None):
    """Safely get a field value from a FIT record."""
    try:
        val = record.get_value(name)
        return val if val is not None else fallback
    except (KeyError, AttributeError):
        return fallback


def parse_fit_bytes(data: bytes, activity_id: int) -> tuple[list, list, list, list]:
    """Parse FIT data (raw or zipped) and return (gps_records, sessions, laps, lengths).

    Each item is a list of dicts ready for pg_writer upsert.
    """
    try:
        import fitparse
    except ImportError:
        log.warning("fitparse not installed, skipping FIT parsing")
        return [], [], [], []

    # Handle ZIP-wrapped FIT files
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
                if not fit_names:
                    log.warning("ZIP contains no .fit files")
                    return [], [], [], []
                data = zf.read(fit_names[0])
        except zipfile.BadZipFile:
            log.warning("Bad ZIP file for activity %s", activity_id)
            return [], [], [], []

    try:
        fitfile = fitparse.FitFile(io.BytesIO(data))
        fitfile.parse()
    except Exception as e:
        log.warning("Failed to parse FIT for activity %s: %s", activity_id, e)
        return [], [], [], []

    records = []
    sessions = []
    laps = []
    lengths = []

    # GPS records
    for rec in fitfile.get_messages("record"):
        ts = _fit_ts(_get_field(rec, "timestamp"))
        if not ts:
            continue
        lat = _sc_to_deg(_get_field(rec, "position_lat"))
        lon = _sc_to_deg(_get_field(rec, "position_long"))
        records.append({
            "activity_id": activity_id,
            "ts": ts,
            "latitude": lat,
            "longitude": lon,
            "altitude": _get_field(rec, "enhanced_altitude") or _get_field(rec, "altitude"),
            "heart_rate": _get_field(rec, "heart_rate"),
            "cadence": _get_field(rec, "cadence"),
            "speed": _get_field(rec, "enhanced_speed") or _get_field(rec, "speed"),
            "power": _get_field(rec, "power"),
            "temperature": _get_field(rec, "temperature"),
            "distance": _get_field(rec, "distance"),
        })

    # Sessions
    for idx, sess in enumerate(fitfile.get_messages("session")):
        ts = _fit_ts(_get_field(sess, "start_time") or _get_field(sess, "timestamp"))
        sessions.append({
            "activity_id": activity_id,
            "session_index": idx,
            "sport": _get_field(sess, "sport"),
            "sub_sport": _get_field(sess, "sub_sport"),
            "start_time": ts,
            "total_elapsed_time": _get_field(sess, "total_elapsed_time"),
            "total_timer_time": _get_field(sess, "total_timer_time"),
            "total_distance": _get_field(sess, "total_distance"),
            "total_calories": _get_field(sess, "total_calories"),
            "avg_heart_rate": _get_field(sess, "avg_heart_rate"),
            "max_heart_rate": _get_field(sess, "max_heart_rate"),
            "avg_speed": _get_field(sess, "enhanced_avg_speed") or _get_field(sess, "avg_speed"),
            "max_speed": _get_field(sess, "enhanced_max_speed") or _get_field(sess, "max_speed"),
            "avg_cadence": _get_field(sess, "avg_cadence") or _get_field(sess, "avg_running_cadence"),
            "max_cadence": _get_field(sess, "max_cadence") or _get_field(sess, "max_running_cadence"),
            "avg_power": _get_field(sess, "avg_power"),
            "max_power": _get_field(sess, "max_power"),
        })

    # Laps
    for idx, lap in enumerate(fitfile.get_messages("lap")):
        ts = _fit_ts(_get_field(lap, "start_time") or _get_field(lap, "timestamp"))
        laps.append({
            "activity_id": activity_id,
            "lap_index": idx,
            "start_time": ts,
            "total_elapsed_time": _get_field(lap, "total_elapsed_time"),
            "total_timer_time": _get_field(lap, "total_timer_time"),
            "total_distance": _get_field(lap, "total_distance"),
            "total_calories": _get_field(lap, "total_calories"),
            "avg_heart_rate": _get_field(lap, "avg_heart_rate"),
            "max_heart_rate": _get_field(lap, "max_heart_rate"),
            "avg_speed": _get_field(lap, "enhanced_avg_speed") or _get_field(lap, "avg_speed"),
            "max_speed": _get_field(lap, "enhanced_max_speed") or _get_field(lap, "max_speed"),
            "avg_cadence": _get_field(lap, "avg_cadence") or _get_field(lap, "avg_running_cadence"),
            "max_cadence": _get_field(lap, "max_cadence") or _get_field(lap, "max_running_cadence"),
            "avg_power": _get_field(lap, "avg_power"),
            "max_power": _get_field(lap, "max_power"),
        })

    # Lengths (swimming)
    for idx, length in enumerate(fitfile.get_messages("length")):
        ts = _fit_ts(_get_field(length, "start_time") or _get_field(length, "timestamp"))
        lengths.append({
            "activity_id": activity_id,
            "length_index": idx,
            "start_time": ts,
            "total_elapsed_time": _get_field(length, "total_elapsed_time"),
            "total_timer_time": _get_field(length, "total_timer_time"),
            "total_strokes": _get_field(length, "total_strokes"),
            "avg_speed": _get_field(length, "avg_speed"),
            "avg_cadence": _get_field(length, "avg_swimming_cadence"),
            "swim_stroke": _get_field(length, "swim_stroke"),
            "length_type": _get_field(length, "length_type"),
        })

    log.debug("FIT activity %s: %d records, %d sessions, %d laps, %d lengths",
              activity_id, len(records), len(sessions), len(laps), len(lengths))
    return records, sessions, laps, lengths
