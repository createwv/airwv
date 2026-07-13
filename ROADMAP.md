# AirWV Roadmap

AirWV has grown from an air-monitoring MVP into a **multi-medium community
environmental platform for West Virginia** — air + water today, soil later —
**live at air.createwv.org**. Phases 0–3 (ingestion, storage, data quality,
trends) and the public site are largely built; recent work added the **Water**
lens (live USGS gauges + Water Quality Portal samples), cross-medium **Events**,
**NPDES** dischargers on Sources, **field-science** spot-check intake, **ozone**
(history + map), and map clustering.

Phases are directional, not date-bound; items move as the sensor network and
partnerships (WVCAG, Create WV, **WV Rivers Coalition**) evolve. Status key:
**✅ done · ◐ partial (built, has follow-ups) · ⬜ open**. A few items are
*blocked on an input* — API points, SMTP/Twilio creds, or a dataset — not code
(called out where they appear).

**Nearest-term / staged:** the community-air **collector** is verified and staged
on the server (enable when points are topped up); **alerts sign-up** and the
**drinking-water intake map** are the next big threads.

---

## Phase 0 — Foundation ✅

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
- [x] **Public alert sign-up UI** — `/alerts` page + nav/home cards: pick an area
      (any WV sensor or a named one) and a plain-language level (unhealthy for
      sensitive groups / everyone / hazardous → PM2.5 35/55/150). Double opt-in
      with confirm + one-click unsubscribe links (`/alerts/confirm`,
      `/alerts/unsubscribe`), honeypot + too-fast + per-IP + dedupe guards, and an
      admin waitlist view (`/api/admin/subscriptions`). **Delivery is SMTP-gated:**
      with creds set it emails the confirmation immediately; until then sign-ups are
      held as a waitlist and confirmed the moment SMTP goes live. *(SMS still open.)*

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
- [x] **Per-area rollups + trend charts** — "How's your area doing?" card on the Air
      dashboard groups community sensors by WV region (worst air first): current
      median, sensors reporting, highest sensor, and a trend arrow (rising/falling %,
      ⚠ worsening). Click an area to chart its daily-median trend. Endpoints
      `/api/areas` + `/api/areas/series` (SQL-aggregated, cached); driven by the main
      metric picker (PM/VOC/temp/humidity — ozone is reference-only). *Trends fill in
      per region as the collector backfills the 43 snapshot-only sensors.*
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
- [x] **⭐ My Sensors (follow list)** — DONE (localStorage) + **📍 Near me** (Air & Water maps: geolocate, zoom, list nearest). *Account-based follow-list later.* — let users star sensors they care about;
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
      simplified map) + nav-card grid + recent-reports feed. **Air** (`/air`, formerly Analysis) =
      the full power dashboard. **Learn** (`/learn`) = air-quality + health education
      (pollutants, AQI scale, who's at risk, what to do). **About** (`/about`) =
      project/data-source/how-to-help. **Admin** = token-gated console.
      *Still to do: alert signup (no UI yet), an updates/news feed, per-area rollups.*
- [~] **Learn page — education hub** — BUILT & growing: tabbed (Basics · Health & AQI ·
      The Data · Laws & Permits · Climate), deep-linkable via `#hash`, all cited. Includes
      pollutant explainers + VOC compound list, EPA AQI scale, a **live "right now in WV"
      snapshot** (reads `/api/sensors`), community-vs-reference + VOC caveat, **Clean Air Act
      / Clean Water Act / permits / violations** with a **WV DEP report box** (spill line
      1-800-642-3074, complaints 1-866-568-6649; verify periodically), a **climate** section
      (Paris/IPCC targets, co-benefits), and two **inline SVG diagrams** (particle-size vs.
      hair, valley temperature-inversion). *Still to develop: **ozone** as a real measured
      pollutant (AirNow carries it — a data/ingest feature, not just copy); a shared/
      expandable **glossary**; WV-specific **emissions/energy** numbers in Climate; possibly
      a "where pollution comes from" diagram; periodic re-verify of the DEP contacts.*
- [~] **Ozone (EPA AirNow reference layer)** — BUILT going-forward: community PurpleAir
      sensors (incl. Flex) can't measure ozone (their gas sensor = the BME688 VOC index we
      already show), so ozone comes from **EPA AirNow** hourly monitors. `ingest airnow` now
      parses OZONE (ppb) into a new `ozone` column; selectable as a metric on Analysis with
      8-hour AQI bands; explained on Learn. WV has ~8 ozone monitors (often more than PM2.5).
      *Still to do: **ozone history** (AirNow only accumulates forward — deep history would
      come from EPA AirData param 44201, which is daily **ppm** and needs unit conversion to
      ppb); an ozone **map layer** (color reference monitors by ozone when that metric is
      picked); summer-ozone context in Learn.*
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
      and **community reports** (`report_ids` → `/air`). *v2: map of event locations,
      auto-surface candidates from the algorithmic `/api/events` detection, per-event
      permalink pages (SEO/press), and richer sensor overlays for captured events.*
- [~] **Reported spills feed (NRC)** — BUILT: `scripts/fetch_nrc_spills.py` pulls the
      **National Response Center** annual .xlsx (US Coast Guard spill hotline, keyless),
      keeps WV, flattens report + material + details + caller → `nrc_spills.json` /
      `/api/nrc-spills`. Answers "where does spill data go?" — the *initial* public report,
      even when a state agency's follow-up samples never become public. On the **Events
      page**: clustered map (blue = reached water, hollow = county-approx) + filterable list
      (reached-water, year). 223 WV reports 2025–26 (91 reached water). Shared
      `airwv/wvgeo.py` county centroids place the ~80% without coordinates. *Next: auto-log
      big ones as curated Events; a monthly refresh; link a spill to nearby measured water.*
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
      `sources.html`/`sources.js`): a browse experience separate from the Air map —
      a sliding **carousel of featured facilities** + a search/category-filterable **grid**
      of all 437; click a card → **detail view** with the facts we cite (name, type,
      operator, category, citation), a **Street View photo** (front-of-business), the
      **nearest air sensors** (distance + bearing), a deep link to the Air
      source-proximity view (`/air?src=`), and **report-a-concern / Report-to-WV-DEP**
      actions. Neutral naming per SOURCE-POLICY. **Imagery = Google Street View Static API**
      (front-of-business, per the request) — needs `AIRWV_GOOGLE_MAPS_KEY` (billing-enabled,
      referrer-restricted); until set, cards show clean category-tile placeholders. *v2:
      permit/status/compliance enrichment (EPA ECHO / WV DEP / FracTracker) to fill the
      "permit & activity" section + active/inactive/pending status; fix categorizer
      (John Amos coal plant currently mislabeled "materials", should be "power").*
- [~] **Facility status / permit layer** — v1 BUILT: a **Compliance & permits**
      section on `/sources` lists WV's **major** regulated facilities from **EPA ECHO**
      (`scripts/fetch_facility_status.py` → `echo_facilities.json`, served by
      `/api/facilities`). Filter by **status** (significant violation / in violation /
      no violation) and **program** (air/water/waste/drinking-water/toxics), with
      summary counts, colored status badges, and per-facility ECHO Detailed Facility
      Report links (250 facilities; 41 in significant violation). Also a **map layer**
      on the Air dashboard: "⚖️ Compliance (EPA ECHO)" draws majors as circle markers
      colored by violation status (toggle whole-layer or by status; leads with violators).
      *Refresh periodically (compliance changes slowly): re-run the script + redeploy — a
      monthly timer could automate it.*
- [~] **Permit lifecycle — WV DEP oil & gas** — BUILT: the forward-looking side ECHO
      can't show. `scripts/fetch_dep_permits.py` pulls WV DEP's O&G permit database
      (tagis.dep.wv.gov ArcGIS) for the pre-production lifecycle — **requested** (permit
      application), **approved** (permit issued, not yet drilled), **under construction** —
      dropping the 150k historical active/plugged wells. → `dep_permits.json`, served by
      `/api/dep-permits`. Shown as a stage-colored **map layer** on the Air dashboard
      (🛢️ O&G permit pipeline) and a filterable **section on Sources** (by stage + county,
      with operator/formation/Marcellus + DEP record links). 651 permits (124 requested).
      *Refresh: re-run the script + redeploy.*
- [~] **Orphaned & abandoned gas wells (the Rutledge story)** ⭐ — a **founding** issue for
      Create WV. v1 BUILT: `scripts/fetch_abandoned_wells.py` → `abandoned_wells.json` /
      `/api/abandoned-wells` — **15,455** abandoned wells statewide, **4,721 orphans** (no
      operator = the state's to plug). Air-dashboard **"🛢️ Abandoned wells"** layer, lazy-loaded
      on toggle (15k clustered; orphans red, operator-abandoned brown; orphan/operator
      sub-filters; popups link the DEP record + note H2S/gas-near-homes risk). The **Rutledge**
      event is seeded on the Events page (H2S + raw gas near Crouch Hollow; sourced WCHS/WSAZ/
      LPM). **Near-homes flag DONE:** `scripts/enrich_wells_proximity.py` grids **Microsoft's
      open US Building Footprints** (~1.05M WV buildings) and tags each well with
      `nearest_building_m` + `near_homes` (<200 m) → **6,053 abandoned wells near a building,
      2,122 of them orphans** (some 2–4 m away, in yards). Layer: near-home wells pop, remote
      ones fade, "🏠 Near homes only" sub-filter, distance in the popup. **Report hook DONE:**
      each well popup has a "🚨 Report a problem with this well" link that opens the community-
      report modal pre-filled + pre-located (domain=air, well id/orphan/county context, H2S-symptom
      prompts) via `window.AIRWV_REPORT_AT` — routed through the normal screen→moderate→notify
      pipeline. **H2S↔VOC tie-in DONE:** `/api/wells-near-sensors` + the Air-dashboard card
      "🛢️ Abandoned wells near our VOC sensors" (`wellsvoc.js`) line up each community sensor's
      **VOC** (relative gas-response index — reacts to hydrocarbons/H2S, not an H2S monitor) with
      the abandoned/orphan wells within ~2/5 km, VOC flagged when above the network median, with
      an explicit correlation-not-causation note. **The EWV Rutledge sensor reads VOC ~105 (above
      the ~84 median) near wells** — the origin story visible in our own data (9 elevated sensors
      sit near wells). **Plugging backlog DONE:** `/api/well-backlog` + Air-dashboard card "🕳️ The
      orphan-well plugging backlog" — 4,721 orphans (2,122 near homes), the years-to-clear math
      (state ~25/yr → ~190 yrs vs federal IIJA ~210/yr → ~22 yrs; IIJA targets only 1,200 by 2030),
      a county backlog bar chart (Ritchie 823…), cited (WV Watch / WV DEP IIJA / US DOI). **Health
      context DONE:** the Learn **Health** tab gained gas/H2S (rotten-egg + olfactory-fatigue danger,
      symptoms, what-to-do), ozone/VOC/benzene, and water-contaminant (lead/nitrate/E.coli/selenium)
      sections, all linked to the map layers + report hooks (CDC/ATSDR/EPA/WV DHHR cited). *Next:
      per-well leak/monitoring data if it ever goes public; a symptom→nearby-hazard reverse guide.*
- [~] **Permit lifecycle — WV DEP mining** — BUILT: coal & mineral mining, WV's other
      big story. `scripts/fetch_dep_mining.py` pulls WV DEP Division of Mining &
      Reclamation permits and keeps the live set — **new** / not-yet-started, **active** /
      renewed, **inactive** — dropping ~8,200 released/revoked historical permits. →
      `dep_mining.json`, `/api/dep-mining`. Dashboard **map layer** (⛏️ Mining permits,
      stage-colored) + Sources **section** (stage + type filters, operator, disturbed vs.
      reclaimed **acreage**). 1,494 permits (240 new, 1,056 active, ~112k acres disturbed).
      *Refresh: re-run the script + redeploy.* **Still open:** WV DEP **air/waste** permit
      lifecycles (thinner status fields — air_quality "All Facilities" is name+coords only);
      FracTracker's own proposed-infrastructure ArcGIS layers; a monthly refresh timer for
      the DEP/ECHO pulls.
- [x] **Map scale** — DONE: Leaflet.markercluster on the dense layers (Air pollution
      sources ~530, Water sites ~2,000) — zoom-based clusters + spiderfy, so the maps stay
      usable statewide. (Sensor/reference layers stay unclustered — colors matter there.)
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

## Phase 7 — Multi-medium: Water, Soil & Field Science 🌊

Broaden from air to **how we affect our whole environment and ourselves** — air,
water, soil — and how those media connect. Vision: help people know what to be
aware of, curb, monitor, and protect from. **Medium-aware architecture:** one
platform, "Air | Water (| Soil)" as lenses that reuse the shared shell / map /
charts / sources / events / reporting, each with its own data model + standards
framing (no single cross-medium AQI). Partner: **WV Rivers Coalition** (already a
Create WV partner — co-supplied some air monitors; want to help them do
people-direct water education and connect it to air).

### Water v1
- [~] **Water data ingestion** — v1 BUILT: `ingest water` pulls **USGS NWIS** real-time
      gauges for WV (keyless) into a tall `water_readings` table (pH, DO, conductance,
      turbidity, temperature, discharge, gage height; ~176 sites), accumulating via a
      timer like AirNow. **Water Quality Portal** now added too (`fetch_water_samples.py`):
      64k WV discrete samples (E. coli, iron/aluminum/manganese, sulfate, nitrate, TDS…) across
      ~1,960 sites — the history/lab side of water. *Still: **WV DEP**
      (waterqualitydata.us — unified USGS NWIS + EPA WQX/STORET + 400 agencies,
      keyless; discrete samples: pH, DO, turbidity, conductivity, E. coli, nutrients,
      metals, temp) and **USGS NWIS/Water Data APIs** (real-time gauges: flow, gage
      height, water temp). WV DEP runs 26 fixed stations (bi-monthly) that also feed WQP.
- [~] **Water map + site detail** — v1 BUILT: `/water` page + nav + Home card — USGS
      gauges on a map, colored by the selected measure (aquatic-life bands for pH/DO/
      conductance/turbidity), click a site to chart recent history (`/api/water/sites`,
      `/api/water/series`). Hourly **systemd water timer** live (accumulating). *Still: drinking-
      water-standard framing; WQP discrete samples; validation/QA.*
- [~] **Water in Events** — DONE (first): events now carry a **medium** (air/water) badge;
      the **2014 Freedom Industries MCHM spill (Elk River, Charleston)** is seeded as the
      first water event (documented, cited). Future spills/advisories + water-data overlays TBD.
- [~] **Water in Sources** — v1 BUILT: `scripts/fetch_water_sources.py` adds 92 WV **NPDES**
      major dischargers (EPA ECHO, keyless) to the Sources page as a **💧 water-discharge**
      category, each linking to its full **ECHO compliance record** (air · water · waste) —
      the 'one facility, both media' payoff. *Still: match NPDES permits onto the existing
      air/TRI entries (dedupe), discharge/violation badges, minors beyond majors.*
- [~] **Coal ↔ water — mine NPDES discharge** — BUILT: the mining→water link.
      `scripts/fetch_coal_npdes.py` pulls WV DEP's Coal NPDES layer (~22k active discharge
      outlets), aggregated by permit (1,434) → each coal operator maps to the streams it
      discharges into, with an EPA ECHO **effluent-charts** link. `/api/coal-npdes`. Shown
      as a **map overlay on the Water page** ("⛏️ Coal discharge", clustered, sized by
      outlet count) and a **Sources section** ("💧 Coal & water"). **303(d) join DONE:**
      each permit's outlet bbox is spatially joined (server-side ArcGIS envelope query,
      no shapely) to WV's 2016 **Clean Water Act 303(d)** impaired-streams layer →
      **1,298 of 1,434 (91%)** sit on an impaired stream, signature acid-mine-drainage
      causes (iron/selenium/pH/aluminum). Impaired dischargers render **crimson** on the
      Water map + get cause chips + an "impaired only" filter on Sources.
- [~] **Measured-values join (loop closed)** — DONE: `Store.water_near(lat,lon,km)` +
      `/api/water/near` (bbox + haversine over the ingested WQP/USGS samples). Coal
      discharge popups on the Water map lazily load the nearest sample sites' **measured**
      iron/aluminum/manganese/sulfate/pH/conductivity, color-coded to the water legend —
      so "listed impaired for iron" becomes "and measured conductance 430–726 µS/cm at
      Buffalo Creek 3 mi away." Overlap: 70% of coal permits within 3 mi of a sample site,
      92% within 5 mi. Now: **permit → 303(d) listing → measured value**. **Selenium
      DONE:** added to the WQP ingest (stored µg/L; `--chars` filter to fetch one
      characteristic) — 3,307 samples / 1,040 sites, on the Water measure picker + coal
      popups; 456 of 518 selenium-listed coal permits have a measured selenium sample
      within 5 mi. **Extended to all facilities:** `water_near` now also powers a
      "Water quality nearby" block in the **Sources facility detail modal** (every curated
      source incl. TRI/chemical) and the **dashboard ⚖️ ECHO popups** for water/drinking-
      water dischargers (e.g. Pleasants Power → Ohio River iron/sulfate/conductivity 1.4 mi
      away). *Next: add more WQP metals as needed; a "measured-water" column on the ECHO/coal
      Sources tables.*

### Cross-medium education & systemic issues
*(Education DONE as the Learn **"Water"** tab: drinking-water source, CSOs, swimming/E. coli, fish advisories, reading 303(d) maps, air↔water. Cited WV DHHR / EPA / Charleston Waterkeeper. Drinking-water **map** of intakes still to build.)*
- [x] **Air ↔ water explainer** — where the same pollution shows up in *both* media and
      where each is exclusive; how they interact (deposition, runoff, shared sources).
- [x] **Combined sewer overflows (CSOs)** — education that it's our *systems*, not only
      individual polluters: aging combined storm+sewage systems dump untreated sewage
      into rivers (e.g., the **Kanawha**) during rain — possibly the main reason to think
      twice about swimming. (Verify current CSO facts/permits before publishing.)
- [~] **Drinking-water systems & SDWA violations** — BUILT: `scripts/fetch_sdwa.py`
      pulls WV public water systems from **EPA ECHO/SDWIS** (keyless) — type, community
      flag, population served, water source, and violation status (health-based, serious
      violator, lead/copper) + ECHO record. → `sdwa_systems.json` / `/api/sdwa` (systems +
      county rollup). On the **Water page**: county bubble map (sized by systems-in-
      violation, click to filter) + filterable system table. **771 active systems, 187 with
      a health-based violation serving 218k people**; answers the Wyoming County crisis
      question directly (10/19 systems, 11 serious violators). Worst: Greenbrier/McDowell/
      Pocahontas/Mingo.
- [ ] **SDWA per-violation detail — contaminant + date** — go past the current health-based
      *flag* to the actual violations: **which contaminant** (nitrate, coliform, TTHM/HAA5,
      lead/copper, arsenic, radionuclides…), the **violation type** (MCL exceedance vs
      treatment-technique vs monitoring), and **when** (begin/end dates, still-open vs
      resolved). Source: EPA **SDWIS `SDWA_VIOLATIONS`** (ECHO `get_effluent`-style detail
      or Envirofacts `data.epa.gov/efservice/SDWA_VIOLATIONS/PWSID/<id>`). Surface a
      per-system violation history (contaminant · type · dates) in the SDWA system row/modal,
      and let the county map/table filter by contaminant. *This is what turns "has a
      violation" into "has nitrate over the limit since March 2025."*
- [~] **Drinking-water source mapping** — flag **where each community's drinking water
      comes from** (e.g., **Elk River intake for Charleston**, down to the withdrawal
      point). Education on source water + a hook for **alerts** (upstream spill → warn
      downstream intakes). Sources: WV Bureau for Public Health source-water assessments.
      *(SDWA layer above now shows each system's source type — ground vs surface.)*
- [x] **Recreation & fish-consumption guidance** — plain-language "can you swim/boat/eat
      the fish?" per river, from **WV DHHR/DEP fish-consumption advisories** + the DEP
      **303(d) impaired-streams / integrated report**; explain **how to read the river
      maps** (why so many segments look "impaired," what the categories mean).

### Field science — trained-scientist spot checks
- [~] **Field-reading intake (verified data, not public reports)** — v1 BUILT: `/field`
      page (mobile-friendly, admin-token-gated) lets trained submitters log an actual
      instrument reading — medium, parameter, value/unit, method, geolocation (GPS or
      map-pick), notes, and a **downscaled meter photo** — stored in `field_readings`
      (trusted; `/api/field-readings`), rendered on a map + list. *Still: submitter
      roles beyond the shared token, QA/verify workflow, overlay on Air/Water maps,
      link to events/sources, trend-over-time per spot.*

### Soil & beyond (later)
- [ ] **Soil maps** — contamination / brownfields / heavy-metal data (EPA, state) as a
      third medium once air + water patterns are proven.
- [ ] Other exposures worth surfacing over time (radon, noise, PFAS-specific, etc.) —
      driven by community concern.

---

### Guiding principles

- **Never lose raw data.** Store what sensors report; flag, don't delete.
- **Real vs. broken.** Always distinguish a genuine pollution event from a
  sensor malfunction.
- **Community-first & open.** Open source, open data, easy for others to run.
- **Secrets stay secret.** API keys and credentials via environment/secret
  managers, never committed.
