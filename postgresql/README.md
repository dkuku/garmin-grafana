# garmin-grafana — PostgreSQL Backend

Alternative backend for [garmin-grafana](https://github.com/arpanghosh8453/garmin-grafana) using **PostgreSQL** instead of InfluxDB.

Uses the same `garminconnect` library for Garmin Connect API access, but stores data in PostgreSQL with proper schema, upsert-based deduplication, and a sync loop that detects new data from your watch.

## Architecture

```
 Fetcher (Python)        PostgreSQL              Grafana
┌─────────────────┐     ┌──────────────────┐    ┌──────────────┐
│ garmin_fetch.py  │────▶│ 26 tables        │◄───│ SQL queries  │
│ garminconnect    │     │ ON CONFLICT      │    │ dashboards   │
│ sync loop 5min  │     │ UPSERT           │    │              │
└─────────────────┘     └──────────────────┘    └──────────────┘
        │
        ▼
  Garmin Connect API
```

## Features

- **26 tables** covering: daily stats, HR/steps/stress/body battery/breathing/HRV intraday, sleep, body composition, activities with GPS/laps/sessions/lengths, VO2 max, race predictions, fitness age, training status/readiness, hill/endurance scores, blood pressure, hydration
- **Smart sync loop** — compares local `max(ts)` vs `get_device_last_used()` to only fetch new data
- **Bulk import mode** — set `MANUAL_START_DATE` + `MANUAL_END_DATE` for historical import
- **FIT file parsing** — downloads and parses GPS records, sessions, laps, swimming lengths
- **Upsert deduplication** — `INSERT ... ON CONFLICT DO UPDATE` on every table
- **Error handling** — rate limit retry (429 → 30min wait), server error skip, auto re-auth
- **raw_json columns** — full API response preserved for future use
- **Grafana dashboard** — 40 panels across 11 sections

## Files

| File | Description |
|------|-------------|
| `config.py` | Configuration from env vars / `.env` file |
| `schema.sql` | DDL for all 26 tables with indexes and grants |
| `pg_writer.py` | PostgreSQL connection pool + per-table upsert methods |
| `garmin_fetch.py` | Auth, sync loop, 19 data fetchers, bulk import |
| `fit_parser.py` | FIT/ZIP parsing for GPS, sessions, laps, lengths |
| `requirements.txt` | Python dependencies |
| `.env.example` | Configuration template |
| `garmin-fetcher.service` | systemd unit file |
| `grafana-dashboard.json` | Grafana dashboard (40 panels) |

## Quick Start

```bash
# 1. Create PostgreSQL database
psql -U postgres -c "CREATE USER garmin WITH PASSWORD 'garmin';"
psql -U postgres -c "CREATE DATABASE garmin OWNER garmin;"
psql -U garmin -d garmin -f schema.sql

# 2. Set up Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your Garmin credentials and PG connection

# 4. First run (interactive — OAuth login)
python3 garmin_fetch.py

# 5. Bulk import history (optional)
MANUAL_START_DATE=2023-01-01 MANUAL_END_DATE=2026-02-22 python3 garmin_fetch.py

# 6. Install as service
sudo cp garmin-fetcher.service /etc/systemd/system/
sudo systemctl enable --now garmin-fetcher
```

## Grafana Setup

1. Add PostgreSQL datasource pointing to your `garmin` database
2. Import `grafana-dashboard.json` or create dashboards with SQL queries like:

```sql
-- HR intraday
SELECT ts AS time, heart_rate FROM heart_rate_intraday
WHERE $__timeFilter(ts) ORDER BY ts

-- Daily steps
SELECT ts AS time, total_steps FROM daily_stats
WHERE $__timeFilter(ts) ORDER BY ts

-- Sleep stages
SELECT ts AS time,
  deep_sleep_seconds/3600.0 AS "Deep",
  light_sleep_seconds/3600.0 AS "Light",
  rem_sleep_seconds/3600.0 AS "REM"
FROM sleep_summary WHERE $__timeFilter(ts) ORDER BY ts
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GARMIN_EMAIL` | required | Garmin Connect email |
| `GARMIN_PASSWORD` | required | Garmin Connect password |
| `GARMIN_PASSWORD_B64` | — | Base64-encoded password (alternative) |
| `PG_HOST` | `192.168.30.104` | PostgreSQL host |
| `PG_PORT` | `5432` | PostgreSQL port |
| `PG_DATABASE` | `garmin` | Database name |
| `PG_USER` | `garmin` | Database user |
| `PG_PASSWORD` | required | Database password |
| `UPDATE_INTERVAL` | `300` | Sync interval in seconds |
| `USER_TIMEZONE` | `Europe/Warsaw` | Timezone for date calculations |
| `MANUAL_START_DATE` | — | Bulk import start (YYYY-MM-DD) |
| `MANUAL_END_DATE` | — | Bulk import end (YYYY-MM-DD) |
| `FETCH_SELECTION` | — | Comma-separated data types (empty = all) |
