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
      per-sensor history via `ingest backfill --days N --average M`
- [x] Rich field set — PM1/2.5/10, **A/B channels**, **confidence**, VOC,
      temp/humidity/pressure, **0.3–10µm particle counts** — captured before backfill
- [x] 25M-point PurpleAir grant + request protocol — `docs/PURPLEAIR-POINTS.md`
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
- [x] PurpleAir A/B channel divergence as a malfunction signal — channel data
      captured + validated across the cluster (Glasgow 99.9% healthy, SC1 1.7%
      noisier). Productize into a command still to do.
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
- [x] Branding — Empower WV logo banner + favicon + brand palette + parallax
      cloud hero. Logo enlarged (~165px floor near 570px wide, scaling to ~20vw
      on desktop, capped in-banner). **Revisit:** fine-tune the exact logo/banner
      sizing curve — the effect wanted is ~20vw on desktop stepping to ~30vw around
      570px; current `clamp(165px,20vw,230px)` approximates it continuously.
- [x] **Master map-layer toggles** — community sensors / 🏭 sources / 📍 monitors
      each toggle on/off (sensors now toggle as one layer like the others).
- [ ] Per-area rollups + trend charts on the dashboard
- [x] **Deployed publicly at air.createwv.org** — live on papa-greatness (Hetzner),
      NPM/Docker reverse proxy + Cloudflare DNS, systemd services/timers (see
      deployment notes). *(Embeddable partner widgets still to build.)*
- [ ] **Location-scoped API for local embeds** — add geographic filters to the read
      API (`?region=` / `?near=lat,lon&radius_km=` / `?bbox=`) so a partner or teammate
      can query just their area's sensors (not all WV) to power a neighborhood widget,
      app, or bot. Foundation already exists (FastAPI + `/api/sensors`, per-sensor
      `region`, `/docs` for free); needs the filter params + CORS + rate limiting +
      documented public endpoints. *A teammate offered to build this — coordinate scope
      (fixed region vs. flexible radius/bbox) and the consumer (widget / app / bot).*

#### Dashboard restructure (modes, layers, labeling) — see [`docs/FRONTEND.md`](docs/FRONTEND.md)

- [x] **Hierarchical layers tree** — collapsible tree (▸ closed by default, parent +
      leaf checkboxes, tri-state). Community Sensors **sub-grouped by WV region**,
      EPA/Reference Monitors, and Pollution Sources with **per-category** toggles.
- [ ] **⭐ My Sensors (follow list)** — let users star sensors they care about;
      persists (localStorage now, account-based later) as a pinned top group.
- [x] **Pollution-source categories** — `category` on all 437 sources (power /
      chemical / oil-gas / materials / waste), distinct map icons + per-category
      toggles in the layers tree. NAICS-based refinement TBD.
- [x] **Name the EPA monitors** — live reference monitors show their real **AirNow
      site names** (`data/airnow_monitors.json`); community sensors keep EWV names.
- [x] **Explicit default time window** — `GET /api/coverage` + a "Showing all data ·
      first → last" line from real min/max timestamps.
- [x] **Multi-page site (was "three modes")** — BUILT: pill mode-nav (Home · Analysis
      · Learn · About · Admin) across a shared shell, with report/feedback modals shared
      site-wide (`_modals.html` + `static/reporting.js`, page-agnostic via
      `window.AIRWV_MAP`; opened by `data-open="report|feedback"`).
      **Home** (`/`) = hub: hero + live air snapshot (stats + PM2.5 headline flag +
      simplified map) + nav-card grid + recent-reports feed. **Analysis** (`/analysis`) =
      the full power dashboard. **Learn** (`/learn`) = air-quality + health education
      (pollutants, AQI scale, who's at risk, what to do). **About** (`/about`) =
      project/data-source/how-to-help. **Admin** = token-gated console.
      *Still to do: alert signup (no UI yet), an updates/news feed, per-area rollups.*
- [~] **Events page (curated air/pollution events by region + time)** — **v1 BUILT**
      (`/events`, `events.html`/`events.js`, `Event` model + `/api/events` +
      `/api/admin/events`): public list of events; click → detail with description, cited
      sources, and — for **captured** events — a Plotly overlay of the involved sensors'
      PM2.5 across the window (before/during/after, event band shaded). Admin console has a
      create/publish/delete form (queue = "Events"). Seeded (`scripts/seed_events.py`) with
      four real events: Peoples Cartage fire (Parkersburg, Jul 2026), Canadian wildfire
      smoke (Jun 2025, captured), WV fall drought wildfires (Nov 2024, captured), and the
      historic Jun 2023 Canada smoke (documented, pre-sensors → no data). Events also carry
      **origin** (likely/suspected cause), **scope** (Local/Regional/Multi-state/Continental),
      **regions affected**, and **links to related facilities** (`source_refs` → `/sources`)
      and **community reports** (`report_ids` → `/analysis`). *v2: map of event locations,
      auto-surface candidates from `/api/events` detection, per-event permalink pages
      (SEO/press), backfill more sensors for the captured wildfire windows.* Original idea below:
- [ ] **Events page (curated air/pollution events by region + time)** — a page that marks
      notable events with a **region, time window, type, description, and citations**, and —
      when the event falls inside our collection window — overlays the **community-sensor
      data around it** (before/during/after time series, the sensors involved, peak levels).
      Two classes: **(a) live-captured** events our sensors actually measured — e.g. the
      **Peoples Cartage fire, Parkersburg, Jul 4–5 2026** (10-min PM2.5 showed a clean
      plume: sharp 05:50 onset, ~70–84 µg/m³ peaks propagating S→N Pkbg1→Pkbg4→Vienna1,
      cleared by 08:00); **(b) historical/documented** events that predate or lack sensor
      coverage — Kanawha Valley "blue haze", the Nitro plant explosion, etc. — shown as
      documented context (cited), clearly labeled "no sensor data" where we have none.
      Connects to: the existing algorithmic `/api/events` episodic detection (auto-surface
      candidates → a maintainer curates them), the reporting pipeline (a confirmed report
      can become an event), the Sources page (link an event to a facility), and wind data
      (downwind attribution, as done for the Parkersburg fire). Data model: an `events`
      table (region, start/end, kind, title, description, lat/lon or bbox, sources[],
      sensor_ids[], status), admin-curated. Turns "something happened here" into a durable,
      browsable, evidence-backed record — a strong community & press asset.
- [x] **Frontend architecture decision** — LOCKED: **Jinja2 templates + vanilla/Alpine,
      no build step**; a Svelte/Vite SPA reserved for `/admin` only if it outgrows that.
- [x] **Jinja2 refactor** — DONE: split the monolithic `INDEX_HTML` string into
      `templates/base.html` (shared shell) + `templates/dashboard.html`, with CSS/JS
      in `static/app.css`/`app.js`. New pages (`/overview`, `/admin`, reporting) now
      just extend `base.html`. The enabling step for modes **and** the reporting UI.
- [ ] **Sensor category metadata** — `network`/`ownership` fields (PurpleAir vs
      EPA/AirNow vs future sensors; Create WV-owned vs partner; indoor/outdoor) to
      power the tree sub-grouping. Foundation: readings already carry `source`.
- [ ] **Beta → launch** — the "under construction" bar is live; drop it (or flip to
      a version/changelog note) when we go public at air.createwv.org.

### Pollution-source context (map layers)

Show potential pollution sources near sensors for perspective — done carefully to
stay factual and avoid defamation risk. See the source-labeling policy below.
*Motivated by a real finding: full 10-min cluster shows industrial-corridor sites
accumulate PM2.5 overnight (1.4–2.0×) while the upriver control stays flat (0.97) —
strongest at Nitro/John Amos (1.98). Mapping the sources makes this legible.*

- [~] **Documented sources layer** — facilities from **EPA TRI** (keyless
      Envirofacts API) + landmarks, cited, on the map — `scripts/fetch_sources.py`.
      Now **statewide (all WV) + cross-border** (OH/PA/MD/VA/KY facilities within
      ~20km of the WV line, so Ohio-River emitters count) — 437 facilities. Still to
      add: EIA power plants, WV DEP permits + O&G wells.
- [~] **Dedicated "Sources & Facilities" page** — **v1 BUILT** (`/sources`,
      `sources.html`/`sources.js`): a browse experience separate from the Analysis map —
      a sliding **carousel of featured facilities** + a search/category-filterable **grid**
      of all 437; click a card → **detail view** with the facts we cite (name, type,
      operator, category, citation), a **Street View photo** (front-of-business), the
      **nearest air sensors** (distance + bearing), a deep link to the Analysis
      source-proximity view (`/analysis?src=`), and **report-a-concern / Report-to-WV-DEP**
      actions. Neutral naming per SOURCE-POLICY. **Imagery = Google Street View Static API**
      (front-of-business, per the request) — needs `AIRWV_GOOGLE_MAPS_KEY` (billing-enabled,
      referrer-restricted); until set, cards show clean category-tile placeholders. *v2:
      permit/status/compliance enrichment (EPA ECHO / WV DEP / FracTracker) to fill the
      "permit & activity" section + active/inactive/pending status; fix categorizer
      (John Amos coal plant currently mislabeled "materials", should be "power").*
- [ ] **Facility status / permit layer** — toggle facilities by status:
      **active / inactive / approved / requesting (planning/pending)** — the permit
      lifecycle regulated emitters go through. Permit info as a separate field
      (permit id/type/status) from operating status. Sources: WV DEP permit
      database, EPA ECHO (compliance/active), FracTracker (proposed/planned sites).
- [ ] **Map scale** — statewide = many markers; add clustering / zoom-based
      thinning so the map stays usable across all of WV.
- [ ] **Linear sources** — commercial rail lines (US DOT/FRA/BTS National Rail
      Network) and major highways / high-traffic roads (WV DOH/DOT AADT traffic
      counts; OpenStreetMap geometry).
- [x] **Source categories + filters** — DONE (see "Pollution-source categories"
      above): `category` on all sources, distinct icons, per-category toggles in the
      layers tree. NAICS-based refinement still TBD.
- [x] **Source-proximity panel ("around a polluter")** — DONE: pick any of the 437
      sources (datalist) → nearest sensors with **distance + 8-way compass bearing**,
      **zone bands** (near-field <1mi / vicinity 1–3mi / downwind 3–10mi), nearest
      community sensor per sector, 1mi & 3mi map rings, and a **"chart nearest"**
      button that pivots the dashboard to the 6 closest. Client-side.
      *Distance bands (scientific): near-field **< 1 mi (~1.6 km)** = clearest for
      ground-level/fugitive emissions; local vicinity **1–3 mi**; tall stacks
      (power plants) can peak **1–10 mi downwind** (plume touchdown), so raw distance
      undersells them.* Needs a bearing calc (haversine already in `validate`) +
      source search.
- [x] **Wind weighting — Level 1 (prevailing wind)** — DONE: 🌀 toggle re-ranks
      sensors by **downwind exposure** = `rose[opposite(bearing)] × exp(−mi/3)`, from
      per-station **wind roses** built off keyless NWS/ASOS data (Iowa Environmental
      Mesonet, `scripts/fetch_wind.py` → `wind_roses.json`; WV is W/SW-prevailing).
      Science + how-we-handle-it documented in
      [`docs/WIND-AND-DISPERSION.md`](docs/WIND-AND-DISPERSION.md).
- [ ] **Wind weighting — Level 2 (event-based)** — correlate a sensor's *elevated*
      readings with the hours it's actually downwind (near-causal; hourly ASOS wind
      in the DB aligned to readings). Operationalizes the overnight-accumulation
      finding (Nitro/John Amos 1.98×). AERMOD-style plume modeling stays out of scope.
- [x] **Community reporting & feedback** — BUILT & LIVE (design v2:
      [`docs/COMMUNITY-REPORTING.md`](docs/COMMUNITY-REPORTING.md)). **Staged
      pipeline**: light auto pre-screen (`reporting.screen`) → published *unverified*
      → maintainer **verify** → *confirmed*. **Broadened scope** (air / water / soil /
      wildlife / suspected-violation / other). **Progressive disclosure** — simple by
      default, optional advanced tools (name an org, contact). **Naming is
      input-allowed but publish-gated**. Keeps ~150 m jitter, per-IP rate-limit +
      honeypot + min-time. Intake `POST /api/reports` + `GET /api/reports` (public
      jittered projection) + 📣 map layer with domain markers.
- [ ] **Community readings** — optional structured measurements (air/water/soil),
      community-submitted + clearly unverified; own map layer once verified. *(table
      exists; entry UI still to build)*
- [x] **Site feedback form** — bug / idea / question about the website, footer
      button → `POST /api/feedback`, routed to maintainers (not mapped) — `feedback` table.
- [x] **Maintainer pipeline: notifications** — new reports/feedback ping **Slack
      &/or Discord** incoming webhooks (`notify/chat.py`; `AIRWV_SLACK_WEBHOOK_URL` /
      `AIRWV_DISCORD_WEBHOOK_URL`), as a background task (best-effort, never blocks a
      submission). Per-message identity ("AirWV Reports" / "AirWV Feedback") + favicon
      avatar; `/admin` **Test notifications** button (`POST /api/admin/notify-test`).
      *Follow-up: Discord avatar not rendering yet — revisit.*
- [x] **Admin / moderation & verification console** — BUILT: token-gated
      (`AIRWV_ADMIN_TOKEN` / `X-Admin-Token`, fails closed) `/admin` page + queues
      (held / unverified / flagged / confirmed / all / feedback) with **verify ·
      publish · approve-org · remove** and feedback triage. *Deferred: enrich/edit,
      merge, respond-to-reporter, CLI mirror — and a real login (shared token for now).*
- [x] **Migrations (Alembic)** — DONE: Alembic set up (batch mode = SQLite ALTER
      support), baseline migration, `AIRWV_DATABASE_URL`-aware env; both DBs stamped.
      Long-standing gap closed. Reporting tables live (`reports` / `readings_community`
      / `feedback` — staged-trust `Report` model). Intake API + admin now built on top.
- [ ] **"Report to WV DEP"** — surface the official channel to report an
      environmental concern/complaint to WV DEP (their complaint form + spill/
      emergency line) from the dashboard, alongside a community report. *Verify the
      exact current URL/number before publishing — do not hardcode an unverified line.*
- [ ] Toggle layers on/off; each marker links to its public-record source.
- [ ] **FracTracker Alliance collaboration** — they already map WV pollution
      sources with strong storytelling ([WV map](https://ft.maps.arcgis.com/apps/instant/sidebar/index.html?appid=da308b503a0140af9e399d092674494f)).
      Explore: (a) overlay/link their layers (ArcGIS feature services) for source
      context, (b) share our sensor data/readings with them, (c) co-tell the
      story (their sources + our measurements). Reduces our own liability by
      leaning on their vetted, sourced datasets.

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

- [x] Reference-monitor data — now sourced **directly from EPA, keyless & quota-free**
      (see [`docs/DATA-SOURCES.md`](docs/DATA-SOURCES.md)): **`ingest airnow`** pulls
      AirNow's hourly file for the **live layer** (110 WV+border monitors, real site
      names), **`ingest airdata`** bulk-imports AQS daily files for **history**
      (232K+ readings, 2007–2024). We reached these via **OpenAQ** (credited) then
      went direct to avoid its quota/ToS. The OpenAQ client (`ingest reference`)
      remains as an optional, quota-safe fallback (hard daily cap + circuit breaker).
- [x] **Sensor-vs-reference validation** (`ingest validate`) — pairs each
      community sensor with its nearest reference monitor, correlates daily-median
      PM2.5 (Pearson r + mean bias). Live: r ≈ 0.73–0.89 across the Kanawha cluster,
      and it auto-flagged the known-bad sensor (196533: r<0, bias +1589 µg/m³).
- [x] Historical reference pull (`reference --start/--end`) to match sensor windows.
- [x] **Validation panel on the dashboard** — the `validate` table live at
      `/api/validate`, color-coded r + bias, with a malfunction flag on absurd bias.
- [x] Plot live reference monitors on the map — AirNow monitors as ringed circles,
      colored by current PM2.5, real site names (OpenAQ + AirData daily are archive,
      excluded from the live map).
- [~] **EPA PurpleAir bias correction** — DONE: `correction.py` (Barkjohn 2021,
      `0.524·PA_cf1 − 0.0862·RH + 5.75`), `validate --correct` + dashboard toggle;
      raw +3–5 µg/m³ bias collapses to ~−1.5 and r improves. STILL TO DO: expose
      corrected PM2.5/AQI everywhere (series, map colors, events, **alerts**) with a
      raw/corrected switch; consider storing a corrected column. Note the malfunction
      sensor (196533) has no RH so it drops from corrected view — keep it visible.
- [ ] **FracTracker collaboration** — pull/link their WV ArcGIS feature services
      (oil & gas, compressors, pipelines) once we have the service URLs or a data share.
- [ ] **Federation / multi-org (CAG runs their own instance)** — instead of Create WV
      requesting points for all 54 network sensors, **WVCAG runs its own AirWV
      instance under its own PurpleAir org + key**, and the two **share/federate
      data** (both run the same open-source app; neither pools keys — cleaner
      governance + each org owns its own grant). Needs: a data-sharing mechanism
      (read-only API export/import or a shared/replicated store), org tagging, and a
      combined public view. **Discuss with Morgan (CAG)** before asking PurpleAir for
      the whole network. Pairs with the tiered points estimate in
      [`docs/PURPLEAIR-POINTS.md`](docs/PURPLEAIR-POINTS.md).
- [ ] Additional community-sensor sources beyond PurpleAir
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
