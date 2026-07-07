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
- [ ] CI: lint + tests on pull requests
- [ ] Issue/PR templates

## Phase 1 — Ingestion & Storage (MVP) 🎯

Reliably pull air data statewide and store it so we never lose history.

- [ ] PurpleAir client (real-time + historical) with API key via secret/env
- [ ] Define the list of WV sensors to poll (start from the Kanawha Valley set,
      expand statewide with WVCAG)
- [ ] Normalize readings into a common schema (sensor id, location, timestamp,
      PM1.0/2.5/10, AQI, VOC, temp/humidity/pressure, source)
- [ ] Time-series storage with a durable history model
- [ ] Scheduled collection (e.g. every N minutes) with retry + backoff
- [ ] Historical backfill from PurpleAir's archive
- [ ] Basic data-quality guards on write (range checks, dedupe)
- [ ] Operational logging + run health

**Exit criteria:** statewide PurpleAir readings flowing on a schedule into
durable storage, with backfilled history and no data loss on transient failures.

## Phase 2 — Data Quality & Anomaly Detection

Separate real signal from sensor noise before anyone relies on it.

- [ ] Spike/anomaly detection (implausible jumps vs. sensor + neighbor history)
- [ ] Sensor health scoring: flag likely malfunctions, stuck values, dropout,
      and channel disagreement (PurpleAir dual-channel A/B divergence)
- [ ] Quarantine/flag suspect readings without deleting raw data
- [ ] Data-quality dashboard/report for maintainers

## Phase 3 — Trends & Analysis

Turn history into insight — what's getting worse and where.

- [ ] Rolling trend computation per sensor / area / pollutant
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
