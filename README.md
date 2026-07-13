# AirWV — West Virginia Air Quality Monitoring

Open-source system for collecting, storing, analyzing, and alerting on air quality
data across West Virginia. AirWV ingests readings from community air sensors
(starting with [PurpleAir](https://www2.purpleair.com/)), captures long-term
history for analysis, detects trends and anomalies, and lets people subscribe to
alerts when the air near them gets bad.

> **Status:** early development. See the [Roadmap](ROADMAP.md).

## Why this exists

AirWV grew out of the [Kanawha Valley Air Quality Monitoring
project](https://createwv.org/projects/air-monitoring/) — a pilot led by
[Create WV](https://createwv.org/) with partners including the
[West Virginia Citizen Action Group (WVCAG)](https://wvcag.org/),
[Appalachian Voices](https://appvoices.org/),
[WV Rivers](https://wvrivers.org/), and
[CIEL](https://www.ciel.org/). That pilot put ~17 PurpleAir sensors into the
"Chemical Valley" around Charleston, an area facing emissions from chemical
plants, thousands of oil & gas wells (many abandoned and leaking VOCs and
hydrogen sulfide), coal dust, and seasonal wildfire smoke.

WVCAG now leads sensor deployment and is expanding the network statewide.
**AirWV is the data layer on top of that hardware** — the software that turns a
scattered set of sensors into a monitored, queryable, alertable, archival system
for the whole state.

## Goals

- **Monitor** air quality across West Virginia in near-real-time.
- **Capture history** — durably store readings for long-term trend analysis
  (not just the rolling window sensor vendors keep).
- **Detect trends** — surface areas and pollutants that are getting worse and
  deserve closer attention.
- **Catch anomalies** — flag implausible spikes that likely indicate a sensor
  malfunction or data error, separately from real pollution events.
- **Alert people** — let residents subscribe to notifications (email, SMS, and
  webhooks to start) when air quality near them crosses a threshold.
- **Stay open** — open-source, open-data, and interoperable, with an eye toward
  institutional archival (e.g. a research-grade backup partner).

## What we track

- **Air Quality Index (AQI)** and the particulate measurements behind it
  (PM1.0 / PM2.5 / PM10).
- **VOCs** (volatile organic compounds) where sensors report them.
- Supporting context: temperature, humidity, pressure — used both as data and
  to sanity-check readings.
- Additional pollutants and sources as we add sensor integrations.

## Architecture at a glance

```
  Sensors                Ingestion            Storage            Analysis            Delivery
 ┌─────────┐   pull    ┌───────────┐        ┌──────────┐       ┌───────────┐       ┌──────────┐
 │PurpleAir│──────────▶│ collectors│───────▶│ time-    │──────▶│ trends &  │──────▶│ alerts   │
 │ (+later │  on a      │ normalize │        │ series   │       │ anomaly   │       │ email/   │
 │  AirNow,│  schedule  │ validate  │        │ history  │       │ detection │       │ SMS/hook │
 │  etc.)  │           └───────────┘        └────┬─────┘       └───────────┘       └──────────┘
 └─────────┘                                     │                                       ▲
                                                 ▼                                       │
                                          ┌────────────┐   read API / map / dashboard ───┘
                                          │  public    │
                                          │  API + web │
                                          └────────────┘
```

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Where every layer comes from — a field guide to public environmental data.**
AirWV is stitched from ~15 public, mostly **keyless** sources (EPA AirNow/AirData/ECHO/
SDWIS/TRI, USGS, the EPA Water Quality Portal, WV DEP's ArcGIS layers, the National
Response Center, Microsoft Building Footprints, …). [**docs/DATA-SOURCES.md**](docs/DATA-SOURCES.md)
is written to be learned from and copied — the access patterns, the gotchas, and a
recipe for adding your own source. The live [`/data`](src/airwv/web/templates/data.html)
page catalogs each one with its freshness for the public. If you're building your own
community-environmental tool, start there.

## Tech stack

- **Language:** Python 3.11+
- **Ingestion:** scheduled collectors per data source
- **Analysis:** pandas / numpy for trend & anomaly detection
- **API:** FastAPI (planned, Phase 5)
- **Storage:** time-series-friendly store (Postgres/TimescaleDB target; SQLite
  for local dev) — see architecture doc

## Quick start (development)

> Nothing to run end-to-end yet — this is the scaffold. These steps get a dev
> environment ready as modules land.

```bash
# 1. Clone
git clone git@github.com:createwv/airwv.git
cd airwv

# 2. Create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Install (editable, with dev extras)
pip install -e ".[dev]"

# 4. Configure secrets
cp .env.example .env
#    then edit .env and add your PurpleAir API key
```

### Configuration & secrets

AirWV reads configuration from environment variables (loaded from a local
`.env` in development). **Never commit secrets.** `.env` is gitignored;
`.env.example` documents what's needed.

| Variable            | Required | Description                                  |
| ------------------- | -------- | -------------------------------------------- |
| `PURPLEAIR_API_KEY` | yes      | PurpleAir read API key                       |
| `AIRWV_DATABASE_URL`| no       | DB connection string (defaults to local SQLite) |

## Getting a PurpleAir API key

The collector needs a PurpleAir **Read** key. It's free to start (new accounts
include ~1,000,000 API points).

1. Sign in at [develop.purpleair.com](https://develop.purpleair.com/) with a
   Google account. *(For an org deployment, use an org-owned account so billing
   and ownership stay with the org, not an individual.)*
2. Create a **Project** (top-right `+ Project`) — e.g. `AirWV`. Projects are just
   containers that group your keys and track usage; no other setup is required.
3. Confirm your **Organization** has points (the free tier is plenty to start).
4. Create an **API Key**: `+ API Key` → select your project → **Type: Read** →
   **Status: Enabled** → leave host/referer restrictions blank (server-side use).
5. Copy the key into your local `.env` as `PURPLEAIR_API_KEY` (never commit it).

A **Write** key is only needed later, to add *private* (non-public) sensors via
PurpleAir groups. Skip it for now.

### Being careful with API points

API calls consume points (roughly: sensors × fields, and history costs more).
To validate the pipeline before spending much, start small:

```bash
python -m airwv.ingest resolve                       # one listing call; see the match report
python -m airwv.ingest collect --limit 3             # poll just 3 sensors
python -m airwv.ingest backfill --days 7 --average 60 --limit 3   # tiny history sample
```

`--limit` caps how many sensors are hit, and hourly (`--average 60`) history is
far cheaper than fine-grained. Once the data looks right, drop `--limit` and
raise `--days`. Watch your balance on the PurpleAir dashboard.

## Running your own instance

AirWV is designed so **each deployment brings its own API key and database** —
you don't pool keys across groups. One organization runs one instance with one
key. If your group wants its own separate system, clone this repo and run it with
your own PurpleAir key and storage; nothing here is tied to a single operator.

## API points & history strategy

PurpleAir bills **API points** (new accounts include ~1,000,000 free). Cost is
roughly `base + fields × per-field`, **per sensor, per call**. Exact per-field
values are in your portal's *Billing → Pricing* tab; the estimates below are
back-calculated from real usage and are good enough for planning.

| Operation | Rough cost | Implication |
| --------- | ---------- | ----------- |
| Statewide `resolve` scan | ~4–10K pts | Expensive — but cached, so run it rarely |
| `collect` (realtime) | ~25 pts/sensor/call | ~750 pts/run for 30 sensors |
| `backfill` (history) | **~10–12 pts/row** | 1 yr hourly ≈ ~95K pts/sensor (~$1) |

> The history rate is measured from real usage (a Glasgow + neighbor 10-min
> backfill of ~84K rows spent ~1M points). Earlier docs guessed ~3/row — it's
> ~3–4× that. A full hourly backfill of all ~54 sensors is ≈ $75, not $20.

**Strategy that keeps you inside the free tier:**

- **Backfill once, then own it.** History costs points on every pull; your local
  DB is free to query forever. Backfill each sensor's history one time, then let
  scheduled `collect` append new readings cheaply.
- **Collect hourly, not every few minutes.** Points scale per sensor per call, so
  tight intervals across many sensors burn the budget fast. The default is hourly
  (`AIRWV_POLL_INTERVAL_SECONDS=3600`) — sustainable for ~30 sensors.
- **Backfill at hourly (`--average 60`).** Finer intervals multiply cost (10-min
  is 6× hourly); reserve those for recent or priority sensors.
- **Test with `--limit`** before any full run (see above).

## Dashboard

A local web dashboard visualizes stored readings (no API key needed — it only
reads the database):

```bash
pip install -e ".[web]"
python -m airwv.web           # open http://127.0.0.1:8000
```

Pick a sensor and metric to see an hourly time series and a time-of-day (local
Eastern) profile. Read API: `/api/sensors`, `/api/series/{id}?field=`,
`/api/diurnal/{id}?field=`.

## Alerts

Subscribe people/systems to threshold alerts and evaluate them against the latest
readings:

```bash
# Alert ops via a webhook when Glasgow PM2.5 hits 35 (unhealthy-for-sensitive)
python -m airwv.ingest subscribe --channel webhook --target https://hooks.example/... \
    --sensor "Glasgow" --field pm2_5 --threshold 35 --quiet-start 22 --quiet-end 7

python -m airwv.ingest alerts            # dry run — shows what would fire
python -m airwv.ingest alerts --send     # actually deliver
```

Two trigger kinds: **threshold** (fire when a value crosses a level) and
**trend** (fire when a field is *rising* by ≥ N% over its record — reuses the
trend analysis):

```bash
python -m airwv.ingest subscribe --channel log --kind trend \
    --sensor "Glasgow" --field voc --threshold 20    # alert if VOC rising ≥ 20%
```

Channels: **log** (always works), **webhook** (POSTs alert JSON), and **email**
(SMTP via `AIRWV_SMTP_*` env vars — set them when ready; see `.env.example`).
Per-subscription rate limiting and quiet hours prevent spam. SMS is planned.

## Contributing

This is a community project — contributions welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) and our
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) — see the file for details.

## Acknowledgments

Create WV, WVCAG, Appalachian Voices, WV Rivers, CIEL, and the residents and
volunteers maintaining sensors across West Virginia.
