# AirWV Architecture

This document describes the intended shape of the system. It's a target, not a
finished state — see the [Roadmap](../ROADMAP.md) for what's built vs. planned.

## Principles

1. **Never lose raw data.** Persist what sensors report. Quality issues are
   handled by *flagging*, never by dropping raw readings.
2. **Real vs. broken.** A pipeline stage explicitly distinguishes genuine
   pollution events from sensor malfunctions.
3. **Pluggable sources.** PurpleAir is the first integration, not the only one.
   New sources implement a common interface.
4. **Secrets out of code.** All credentials come from the environment / a
   secret manager. Nothing sensitive in the repo.

## Data flow

```
 ┌────────────┐    ┌─────────────┐    ┌────────────┐    ┌──────────────┐    ┌────────────┐
 │  Sources   │───▶│  Ingestion  │───▶│  Storage   │───▶│   Analysis   │───▶│  Delivery  │
 │ (PurpleAir)│    │ collect +   │    │ raw +      │    │ trends +     │    │ alerts +   │
 │            │    │ normalize + │    │ normalized │    │ anomalies +  │    │ API + web  │
 │            │    │ validate    │    │ time-series│    │ sensor health│    │            │
 └────────────┘    └─────────────┘    └────────────┘    └──────────────┘    └────────────┘
```

### 1. Sources

Each source is an adapter that knows how to talk to one upstream provider and
emit a list of normalized `Reading`s. Sources implement a common `Source`
interface (`src/airwv/sources/base.py`). First: `PurpleAirSource`.

### 2. Ingestion

Scheduled collectors call sources on an interval, with retry/backoff on
transient failures. Responsibilities:

- Poll each configured source
- Normalize into the common reading schema
- Apply write-time guards (range sanity checks, dedupe by sensor+timestamp)
- Persist raw + normalized records

### 3. Storage

Time-series-oriented store. Development defaults to **SQLite** for zero-setup;
production targets **PostgreSQL + TimescaleDB** (hypertables suit the
sensor-reading-over-time access pattern). The storage layer is abstracted so the
backend can change without touching ingestion or analysis.

Two logical tiers:

- **Raw**: exactly what the source returned (audit trail, reprocessable).
- **Normalized readings**: the common schema everything downstream reads.

Long-term, a research-grade archival partner may mirror this store for
preservation (Roadmap Phase 6).

### 4. Analysis

Runs over stored history (batch and/or incremental):

- **Anomaly detection** — implausible spikes vs. a sensor's own history and its
  neighbors; PurpleAir A/B channel divergence as a malfunction signal.
- **Sensor health** — stuck values, dropout, persistent disagreement → a health
  score per sensor.
- **Trends** — rolling per-sensor / per-area / per-pollutant trends; flag
  degrading areas as "watch."

### 5. Delivery

- **Alerts** — evaluate subscriptions against new readings/trends and notify via
  email, SMS, and webhooks.
- **API + web** — FastAPI read API and a public map/dashboard.

## Common reading schema (draft)

| Field         | Type      | Notes                                        |
| ------------- | --------- | -------------------------------------------- |
| `source`      | str       | e.g. `purpleair`                             |
| `sensor_id`   | str       | source-native id                             |
| `lat`,`lon`   | float     | sensor location                              |
| `ts`          | datetime  | reading timestamp (UTC)                      |
| `pm1_0`       | float?    | µg/m³                                        |
| `pm2_5`       | float?    | µg/m³                                        |
| `pm10`        | float?    | µg/m³                                        |
| `aqi`         | float?    | derived/reported AQI                         |
| `voc`         | float?    | where available                              |
| `temperature` | float?    | °C or °F (normalized)                        |
| `humidity`    | float?    | %                                            |
| `pressure`    | float?    | hPa                                          |
| `raw`         | json      | original payload for reprocessing            |
| `quality`     | enum      | `ok` / `suspect` / `malfunction` (Phase 2)   |

This schema will firm up during Phase 1.

## Configuration

All config via environment variables (see `.env.example`). Key ones:

- `PURPLEAIR_API_KEY` — PurpleAir read key (secret)
- `AIRWV_DATABASE_URL` — storage connection (defaults to local SQLite)
