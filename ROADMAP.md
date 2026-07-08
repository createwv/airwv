# AirWV Roadmap

This roadmap sequences the build so that each phase stands on a working
foundation. It reflects the current priority: **get reliable ingestion and
durable storage first**, because trend detection, anomaly flagging, and alerts
all depend on having clean, historical data to work from.

Phases are directional, not date-bound. Items may move as the sensor network and
partnerships (WVCAG, Create WV, and a potential research-grade archival partner)
evolve.

---

## Phase 0 — Foundation ✅ (in progress)

Repo hygiene and a clean open-source starting point.

- [x] Git repository under the `createwv` org
- [x] README, roadmap, architecture doc
- [x] License (MIT), Contributing guide, Code of Conduct
- [x] Python project scaffold, `.gitignore`, `.env.example`
- [x] CI: lint + tests on push/PR (ruff + pytest, Python 3.11/3.12)
- [ ] Issue/PR templates

## Phase 1 — Ingestion & Storage (MVP) ✅

Reliably pull air data statewide and store it so we never lose history.
*Validated against the live API: 30 sensors resolving, realtime + backfill both
confirmed capturing real data.*

- [x] Normalize readings into a common schema (sensor id, location, timestamp,
      PM1.0/2.5/10, AQI, VOC, temp/humidity/pressure, source) — `Reading`
- [x] Durable storage with idempotent, deduped writes (SQLite dev / Postgres prod) — `Store`
- [x] WV sensor registry (PII-free, private) — 54 deployed PurpleAir sensors
      across 12 counties, keyed by device id, installing org retained — `registry.py`
- [x] PurpleAir current-readings client (`/sensors`) — `PurpleAirSource`
- [x] **Resolve device names → PurpleAir `sensor_index`** — built: match registry
      names against a WV bounding-box listing, cache `device_id → sensor_index`
      privately, report unmatched — `resolve.py`. *(run `ingest resolve` w/ key)*
- [x] **End-to-end collector** — `registry → resolve → PurpleAirSource → Store`,
      as `python -m airwv.ingest` (`resolve` + `collect`) — `ingest.py`.
      *(needs API key to capture live data)*
- [x] Scheduled collection with retry + backoff — `ingest run` loop plus systemd
      timer / cron deploy configs (`deploy/`)
- [x] Range/sanity guards on write — out-of-range values flagged `suspect`
      (never dropped, raw preserved) — `validate.py`
- [~] Operational logging + run health — structured logging + suspect-flag
      warnings in place; sensor dropout/last-seen alerting still pending
- [x] Historical backfill from PurpleAir's archive — windowed, resilient
      per-sensor history via `ingest backfill --days N --average M` (VOC included)
- [x] Sensor scoping + timeline cherry-pick — `--org` / `--sensor` to focus a
      subset, `--start` / `--end` for arbitrary date ranges — `ingest.py`
- [x] Reach offline sensors — resolve with `max_age=0` so currently-down units
      still resolve and their pre-down history is pullable by index (read key
      only). Took resolution 30→52/54; unlocked Glasgow. — `sources/purpleair.py`
- [ ] Owner path — only needed now for the ~2 truly-private/renamed sensors, and
      as the *free* pull route for Create WV-owned units (add by MAC + owner_email
      to a group; needs a write key). No longer required for offline sensors.

**Exit criteria:** statewide PurpleAir readings flowing on a schedule into
durable storage, with backfilled history and no data loss on transient failures.

### Rollout scope

- **Now — Create WV / Kanawha Valley sensors** (14; owned, so likely free to pull).
  Test and prove the system here first.
- **Next — CAG / EWV sensors** (40): either fold in (buy points — cheap, ~$12–20
  one-time for full hourly history) or have CAG run their own instance with their
  own key. Leaning toward one paid instance for simplicity; don't burden orgs.
- **Later — all WV public sensors** (~563) if a statewide net is wanted.

## Phase 2 — Data Quality & Anomaly Detection 🎯

Separate real signal from sensor noise before anyone relies on it.
*Run with `python -m airwv.ingest analyze` (no API cost — reads stored data).*

- [x] Spike detection — robust (median/MAD) z-score vs. a sensor's own history —
      `analysis/anomaly.py`. Already surfaced a real malfunctioning unit.
- [x] Stuck-value detection (frozen channel) — `analysis/anomaly.py`
- [x] Sensor health scoring — dropout/offline, missing channels, degraded status
      — `analysis/health.py`
- [ ] Neighbor cross-check — corroborate spikes against nearby sensors to
      distinguish a real event from a single-sensor malfunction
- [ ] PurpleAir A/B channel divergence as a malfunction signal
- [ ] Persist findings — flag anomalous rows / store a health record (currently
      read-only reporting)
- [ ] Data-quality dashboard/report for maintainers

## Phase 3 — Trends & Analysis

Turn history into insight — what's getting worse and where.

- [ ] Rolling trend computation per sensor / area / pollutant
- [ ] Time-of-day / day-of-week pattern analysis — bucket readings by hour to
      surface recurring diurnal patterns (e.g. evening/overnight VOC or PM
      elevation at specific sites), corroborated with neighbors + weather
- [ ] "Areas to watch" — automatic flagging of degrading trends
- [ ] AQI and VOC trend tracking over selectable windows
- [ ] Comparative/regional context (neighboring sensors, statewide baseline)
- [ ] Exportable analysis datasets

## Phase 4 — Alerts & Subscriptions

Get warnings to the people who need them.

- [ ] Subscription model (who wants what, where, at which thresholds)
- [ ] **Email** notifications (thresholds + digests)
- [ ] **SMS** notifications (e.g. via Twilio-class provider)
- [ ] **Webhooks** for partner/org integrations (Slack/Discord/custom)
- [ ] Threshold + trend-based triggers (not just instantaneous AQI)
- [ ] Alert deduping / rate limiting / quiet hours
- [ ] Subscription management (opt-in, confirm, unsubscribe)

## Phase 5 — Public API & Dashboard

Make the data visible and usable.

- [ ] Read API (FastAPI) for current + historical readings
- [ ] Public map of live AQI across WV
- [ ] Per-sensor and per-area detail views with trend charts
- [ ] Embeddable widgets for partner sites

## Phase 6 — Interoperability & Archival

Grow the network and secure the record.

- [ ] Additional sources (EPA AirNow, other community sensors, reference monitors)
- [ ] Research-grade archival / backup partnership (e.g. an institutional
      environmental data store) for long-term preservation
- [ ] Open-data exports and a documented public dataset
- [ ] Public API documentation and rate limiting

---

### Guiding principles

- **Never lose raw data.** Store what sensors report; flag, don't delete.
- **Real vs. broken.** Always distinguish a genuine pollution event from a
  sensor malfunction.
- **Community-first & open.** Open source, open data, easy for others to run.
- **Secrets stay secret.** API keys and credentials via environment/secret
  managers, never committed.
