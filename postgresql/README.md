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
- **Grafana dashboard** — 62 panels converted from upstream InfluxDB dashboard
- **Explorer Tiles dashboard** — StatsHunters-style tile map from GPS activity data

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
| `grafana-dashboard.json` | Main Grafana dashboard (62 panels, converted from upstream) |
| `grafana-tiles-dashboard.json` | Explorer Tiles dashboard (GPS tile map) |

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
2. **Important**: The `database` field must be set in both the top-level config and inside `jsonData` (required by Grafana 12+):
   ```yaml
   # /etc/grafana/provisioning/datasources/garmin.yaml
   apiVersion: 1
   datasources:
     - name: Garmin-PostgreSQL
       type: postgres
       url: your-pg-host:5432
       database: garmin
       user: grafana_garmin
       jsonData:
         database: garmin
         sslmode: disable
         postgresVersion: 1600
       secureJsonData:
         password: your-password
   ```
3. Import `grafana-dashboard.json` (main health dashboard, 62 panels)
4. Import `grafana-tiles-dashboard.json` (explorer tiles map)

### Query notes for PostgreSQL

Tables with `DATE` type `ts` columns (daily_stats, sleep_summary, body_composition, etc.) require `::timestamptz` casts for Grafana's time_series format:

```sql
-- DATE columns need cast
SELECT ts::timestamptz AS time, total_steps FROM daily_stats
WHERE $__timeFilter(ts::timestamptz) ORDER BY ts

-- TIMESTAMPTZ columns work directly
SELECT ts AS time, heart_rate FROM heart_rate_intraday
WHERE $__timeFilter(ts) ORDER BY ts
```

## Explorer Tiles Dashboard

A [StatsHunters](https://www.statshunters.com/)-style dashboard that computes explorer tiles from your GPS activity data. Divides the world into zoom-level-14 grid squares (~2km) and shows which ones you've visited.

**Features:**
- Total tiles visited, cumulative growth over time
- All-time tile map with visit count heatmap
- New vs old tiles overlay (last 90 days highlighted)
- New tiles per month, tiles per activity type
- GPS activity heatmap
- Configurable zoom level (12–15, from ~10km to ~1km squares)

**Tile computation** is done entirely in SQL using the [slippy map](https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames) formula:

```sql
floor((longitude + 180.0) / 360.0 * power(2, zoom))::int AS tile_x
floor((1 - ln(tan(radians(lat)) + 1/cos(radians(lat))) / pi()) / 2 * power(2, zoom))::int AS tile_y
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
