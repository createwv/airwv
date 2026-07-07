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

Get a PurpleAir API key at <https://develop.purpleair.com/>.

## Contributing

This is a community project — contributions welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) and our
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) — see the file for details.

## Acknowledgments

Create WV, WVCAG, Appalachian Voices, WV Rivers, CIEL, and the residents and
volunteers maintaining sensors across West Virginia.
