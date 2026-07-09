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

## Phase 3 — Trends & Analysis ✅

Turn history into insight — what's getting worse and where.
*(One item — a second neighbor + rural control — is parked on API points, not code.)*

- [x] Trend computation per sensor/pollutant — linear fit on daily medians with
      correlation gating — `analysis/trends.py`, `ingest trends`, dashboard trend line
- [x] "Areas to watch" — auto-flag pollutants trending up (`is_worsening`).
      Glasgow flagged rising: PM2.5 +117%, VOC +79% over its 2024 record.
- [x] Time-of-day / day-of-week pattern analysis — local-hour bucketing of any
      field — `analysis/patterns.py`, `ingest patterns`. (Glasgow VOC: ~2.1×
      evening/overnight vs midday, flat across weekdays → consistent with valley
      nocturnal inversion + a persistent source, not business-hour timing.)
- [x] Neighbor comparison — `ingest compare` (day/night amplitude across sensors).
      Glasgow PM2.5 night/day 1.6 vs neighbor Montgomery 1.0 → Glasgow-specific
      overnight buildup (calibrated PM2.5). Seasonal: year-round, not winter-peaked.
- [x] Residual/episodic event detection — `ingest events` (de-trend by local hour
      → residual robust-z) — `analysis/events.py`. Surfaced a 2024-09-24 PM2.5=1086
      morning event at Glasgow. (Real-vs-glitch verification needs neighbor/A-B.)
- [ ] Second neighbor (Belle) + rural control — blocked on API points (free tier
      exhausted; needs points top-up or PurpleAir grant)
- [x] AQI/VOC/PM trend tracking over selectable windows — `ingest trends --field`,
      dashboard date-range + trend line
- [x] Comparative/regional context — `ingest baseline`: each sensor's median vs the
      network baseline, flags sites above peers — `analysis/regional.py`
- [x] Exportable datasets — `ingest export --format csv|json` + dashboard "Download
      CSV" (`/api/export/{id}.csv`) — `export_utils.py`

## Phase 4 — Alerts & Subscriptions 🎯

Get warnings to the people who need them.

- [x] Subscription model (channel/target/sensor/field/threshold) — `Subscription`
- [x] **Email** notifications — SMTP via env (`notify/email.py`); *you supply creds*
- [x] **Webhooks** for partner/org integrations (Slack/Discord/custom) — `notify/webhook.py`
- [x] Alert deduping / rate limiting / quiet hours — per-subscription in `alerts.evaluate`
- [x] `subscribe` + `alerts` CLI (dry-run by default; `--send` to deliver)
- [x] Trend-based triggers — alert when a field is rising by >= N% (`kind=trend`,
      reuses Phase 3 `linear_trend`). Glasgow VOC (+79%) fires a +20% subscription.
- [ ] **SMS** notifications (Twilio-class provider) — channel stubbed, not wired
- [x] Scheduled evaluation — the `run` loop evaluates + delivers alerts after each
      collection (`--no-alerts` to disable)
- [ ] Subscription management UI — opt-in, confirm, unsubscribe (CLI-only for now)

## Phase 5 — Public API & Dashboard 🎯

Make the data visible and usable.

- [x] Read API (FastAPI) — `/api/sensors` (w/ coords+color), `/api/series`,
      `/api/diurnal`, `/api/events` — `web/app.py`
- [x] Dashboard (v1) — time series + time-of-day profile with friendly names
- [x] Dashboard (v2) — Leaflet **sensor map** (PM2.5-colored, clickable) + **event
      markers** overlaid on the time series
- [x] Dashboard (v3) — **multi-sensor overlay** (time series + diurnal), **date-range
      picker**, and a **day/night compare table** — `/api/compare`
- [x] Health guide + EPA/PurpleAir color bands (PM2.5 + VOC), clickable events,
      About/repo section — `/api/guide`
- [x] Deploy configs — `airwv-web.service` + Caddy runbook (`deploy/README.md`)
- [ ] Branding — apply org logo + palette (Empower WV / Create WV assets)
- [ ] Per-area rollups + trend charts on the dashboard
- [ ] Embeddable widgets for partner sites; deploy publicly at air.createwv.org

### Pollution-source context (map layers)

Show potential pollution sources near sensors for perspective — done carefully to
stay factual and avoid defamation risk. See the source-labeling policy below.

- [ ] **Documented sources layer** — plot facilities from authoritative public
      records: EPA FRS/ECHO/TRI facilities, EIA/EPA power plants (e.g. John Amos),
      WV DEP permitted emitters, WV DEP oil & gas wells. Factual, cited.
- [ ] **Linear sources** — commercial rail lines (US DOT/FRA/BTS National Rail
      Network) and major highways / high-traffic roads (WV DOH/DOT AADT traffic
      counts; OpenStreetMap geometry).
- [ ] **Suspected/community-reported layer** — clearly separate tier for
      unverified concerns, hedged language + basis cited (never asserted as fact).
- [ ] Toggle layers on/off; each marker links to its public-record source.

**Source-labeling policy ("constitution")** — draft into `docs/SOURCE-POLICY.md`:

1. **Facts over accusations.** Only plot from authoritative public records; state
   facts (name, type, permit/emissions per the cited record) — do not assert a
   facility caused any sensor's readings.
2. **Two tiers, clearly separated.** *Documented* (public record, factual) vs.
   *community-reported/suspected* (unverified, labeled as such, hedged: "reported
   concern," "potential," never "responsible for").
3. **Cite everything.** Every marker links to its EPA/DEP/DOT source record.
4. **Neutral language.** "Permitted emitter of X (EPA TRI)" not "polluter."
5. **Right of reply / correction** process; visible map disclaimer.
6. Legal footing: truthful statements of public record + clearly-labeled
   hypotheses. Avoid stating false facts or implying wrongdoing as fact.

## Phase 6 — Interoperability & Archival

Grow the network and secure the record.

- [ ] Additional sources (EPA AirNow, other community sensors, reference monitors)
- [ ] Research-grade archival / backup partnership (e.g. an institutional
      environmental data store) for long-term preservation
- [ ] Open-data exports and a documented public dataset
- [ ] Public API documentation and rate limiting
- [ ] **Schema migrations** (e.g. Alembic) — `create_all` adds new tables but
      won't alter existing ones (adding a column needs a migration for live DBs)

---

### Guiding principles

- **Never lose raw data.** Store what sensors report; flag, don't delete.
- **Real vs. broken.** Always distinguish a genuine pollution event from a
  sensor malfunction.
- **Community-first & open.** Open source, open data, easy for others to run.
- **Secrets stay secret.** API keys and credentials via environment/secret
  managers, never committed.
