# Autopilot plan (airwv)

CZadmin's autopilot works the items Corey tagged `[auto]`, top to bottom, on branch `corey/auto`. Rules and goals:
CZadmin `projects/airwv/CHARTER.md`. Workers tick an item when it's done and never add `[auto]` tags.
Direction: `~/Sites/clients/airwv-direction-review-2026-11-05.md` (outside the repo; approved by Corey 2026-11-05).

## Now
- [ ] Screen sizes in the harness (Corey 2026-11-05): screenshot every page or screen the harness covers at all sizes in `~/.czadmin/tools/devices.json` (phone-small 360x780, phone 390x844, phone-landscape 844x390, tablet 820x1180, desktop 1280x800, desktop-wide 1920x1080; touch + pixel ratio on phones and tablets), and make the report flag anything cut off, overlapping, unreadable or scrolling sideways at any size. Fix nothing yet; list what breaks as proposed items [auto:S]
- [x] Product harness: a pytest + Playwright smoke suite that boots the app on the local SQLite copy, screenshots the key pages at 390/1280, asserts a reading is visible above the fold, nav count, map bounds, and no console errors. Every later item cites it. Do first. [auto:M]
- [x] Direction spec: Corey approved the review's direction on 2026-11-05 (six-noun model, Basic/Advanced toggle, nav collapse, demo place Rutledge). Write the one-page spec in docs/ from the review so later items build on it [auto:S]
- [x] Unified read model, views + `/api/query`: SQL views (`site`, `measurement`, `record`) over the existing tables and JSON layers; `/api/near` reimplemented on them with a snapshot test proving identical output. (sized L in the review: fit it in one M job or split it, and say so) [auto:M]
- [ ] URL-state everywhere: a shared `state.js` that reads/writes place, time, param, layers, view to the query string; Home/Air/Water/Nearby adopt it. [auto:M]
- [x] Place picker + "my place now" basic view: new `/place` route and template; status lines per medium from `/api/query`; "what changed" from the same. [auto:M]
- [ ] Depth toggle + advanced view: `view=adv` shows today's /air controls under the place; /air redirects to `/place?…&view=adv`. [auto:M]
- [ ] Site pages: `/site/<kind>/<id>` for sensor, monitor, gauge, facility, water system, well, spill; popups and lists link to them. [auto:M]
- [x] Data centers, research (Corey 2026-11-05: residents are worried about water, energy and noise): with WebSearch /
      WebFetch, list every WV data center and crypto-mining site, proposed, permitted or operating, and what public
      records say about each: operator, location, status, power (MW, utility or on-site generation, PSC dockets,
      microgrid filings), water (source, withdrawals from WV DEP water-use reports or permits, cooling type), backup
      generators (WV DEP air permits), noise (ordinances, complaints, any measurements), and what it's used for:
      generative AI / cloud / crypto mining / storage / government or surveillance / enterprise / unknown, with the
      evidence and a confidence for each. Write `docs/DATA-CENTERS.md` (sources, what exists, what doesn't) and
      `data/data_centers.json` with the usual provenance block; "not disclosed" is a real value, not a blank [auto:M]
- [x] Data centers on the site: a `data_center` site kind (same site page as the rest) and a layer; on the basic place
      view a plain card when one is near ("what we know / what's not disclosed" for water, energy, noise and purpose,
      each with its source), and on the advanced view the full record and filings. Where the data doesn't exist,
      say so and say how people can ask (PSC dockets, DEP permit comments, public records requests) [auto:M]
- [x] Roadmap: a "Data centers" section (water, energy, noise, purpose, what's undisclosed) and a later
      "Noise and light pollution" medium (night-sky brightness from satellite data, community noise reports and
      sensors) in ROADMAP.md, placed after Land [auto:S]
- [x] Nav collapse + terms pass: six-item nav, "More" menu, glossary file, rename Sources→Facilities, emoji removed, Admin to footer. [auto:S]
- [x] Map bounds + y-axis + default sensor: items from §6. [auto:S]
- [ ] /sources pagination + /water heading fix + haze opt-in. [auto:S]
- [ ] API caching, gzip, bbox for heavy layers, self-hosted libs. [auto:M]
- [x] Accessibility pass: alt/aria/focus on custom controls, reduced-motion, contrast check in the harness. [auto:M]
- [x] Explore v1 (query builder UI): form over `/api/query`, table + CSV + API link. [auto:M]
- [ ] (session with Corey) Postgres + Timescale migration — session with Corey, L: steps 1–6 from ROADMAP; touches prod. SQLite-ism fixes beforehand are.
- [ ] (session with Corey) Read-only SQL role + `/explore/sql` — session (needs Postgres), M.
- [x] Nightly public dumps + rate limit + CORS after Postgres; S on SQLite for the dumps alone. [auto:S]
- [ ] Place-based alerts, any medium: subscriptions keyed to place + rule; spill/violation triggers; SMTP go-live is a session item. [auto:M]
- [ ] (session with Corey) Land medium ingest (Superfund/RCRA/AML) — session S to fetch (network), then to model and surface.
- [x] Neighbor cross-check + auto event candidates. [auto:M]
- [ ] (session with Corey) Demo script + partner walkthrough S with Corey: the five-step narrative from §4, one URL per step.
### From job proposals
- [x] Add a glossary file (`docs/GLOSSARY.md`) with the six terms (sensor, monitor, gauge, sample site, facility, data source), so the nav and terms pass can link to it [auto:S]
- [x] Move `/api/areas` and `/api/water/near` onto `/api/query` (add an area filter and a time window for measurements), with snapshot tests like `/api/near`'s [auto:M]
- [ ] A small sites table for AirNow reference monitors and readings-table sensors, filled at ingest, so they join the `site` view without a GROUP BY over `readings` [auto:S]
- [x] Remove the exact-duplicate rows from `abandoned_wells.json` in its fetch script (this changes the Nearby list, so Corey decides) [auto:S]
- [x] An `/api/query` series mode: `site_id` + `param` + `from`/`to` returns measurement rows plus a CSV link, as the base for Explore v1 [auto:M]
- [ ] Re-queue "Depth toggle + advanced view" once the Place picker branch (`czjob/6d558c`) is merged into `corey/auto`. [auto:M]
- [ ] Data centers, primary records: pull DEP DAQ engineering evaluations for R14-0042, R13-3713/3714/3715 and DEP water-use (large-quantity user) reports; fill the "none found" water and generator fields in `data_centers.json`. [auto]
- [ ] Data centers, PSC: search the PSC case system for each operator and confirm the 24-0611-E-T-PW final order; add case links to `dockets`. [auto]
- [ ] Data centers, watch list: a dated "open comment periods / hearings" list in DATA-CENTERS.md (e.g. R14-0042 comments due 2026-11-13), refreshed from DEP notices. [auto]
- [ ] Data centers, geocode: add verified coordinates (from permits or parcel records) for sites that have a street address, with `location_precision` filled. [auto]
- [ ] Per-fact source keys in `data_centers.json` (a source index next to each field), so each card line links its own source [auto:S]
- [ ] When the Place picker and Depth toggle merge, show the data-center card on `/place` and the full record under `view=adv` [auto:S]
- [ ] Move `/api/near` and the other site kinds onto `/site/<kind>/<id>` (the existing "Site pages" item can use `site.html`) [auto:M]
- [ ] Show dated open comment periods (e.g. R14-0042 comments due 2026-11-13) on the data-center card once the watch-list item lands [auto:S]
- [ ] Harness: add the `/site/<kind>/<id>` pages (one per kind, all screen sizes) to `tests/product/run.sh` once the harness exists. [auto]
- [ ] Site pages: a recent-readings chart for sensors, monitors and gauges, using the `/api/query` series mode. [auto]
- [ ] Hide the statewide haze pop-up on `/place`, or scope it to the chosen place, so it doesn't contradict the air line [auto:S]
- [ ] Point the status lines at `/site/<kind>/<id>` once site pages cover sensors, gauges and sample sites [auto:S]
- [ ] Make `/` show "my place now" when a place is set (in the URL or saved in the browser), otherwise statewide as now [auto:S]
- [ ] Site pages for permits (coal discharge, DEP mining, oil & gas): `/site/permit/<id>`, with the permit stage and 303(d) impaired-stream records. [auto:S]
- [ ] Link the Air-map curated-source markers to `/site/facility/tri-<n>` by giving `/api/sources` rows a stable id. [auto:S]
- [ ] "Related nearby" on site pages: the 5 nearest sites of other kinds from `/api/query`, each linking to its page. [auto:S]
- [ ] Harness: open the "More" menu and screenshot it at every device size; flag it if the dropdown is cut off. [auto]
- [ ] Terms pass, part 2: apply `docs/GLOSSARY.md` wording across the page text (sensor vs monitor vs gauge; never "source" alone) and remove emoji from headings and buttons if you want that. [S] [auto]
- [ ] Link the footer Glossary to an on-site `/learn#glossary` that merges the six terms with Learn's existing glossary. [S] [auto]
- [ ] Put local copies of Leaflet, markercluster and Plotly in `static/vendor/` so the harness's map and chart checks run (also part of "self-hosted libs"). [auto:S]
- [ ] Tighten the harness map check to "the map's first view contains West Virginia and is at most about 2× its size", at every screen size. [auto:S]
- [ ] Show sensor names on the local database copy, not bare IDs, so screenshots read like production. [auto:S]
- [ ] Move /air's controls on phone below the map into a collapsed "Filters" sheet (the next §6 item). [auto:S]
- [ ] Harness: run the a11y checks with a dialog open and a map popup open (report modal, facility detail, a sensor popup) [auto:S]
- [ ] Map markers: give the Leaflet markers that open popups keyboard access and names (`keyboard: true`, `title`/`alt`) once Leaflet is vendored for the harness [auto:S]
- [ ] Skip link ("Skip to content") and a `<main>` landmark in `base.html` [auto:S]
- [ ] Explore v2: a "measurements" option using the `/api/query` series mode (site + param + from/to), with table, CSV and API link [auto:M]
- [ ] Move the place list from `place.js` and `explore.js` into the shared `state.js` when URL-state lands [auto:S]
- [ ] Explore: a county filter and a "where value over N" filter on `/api/query`, with tests [auto:M]
- [ ] CORS + rate limit on `/api/*` and `/data/dumps/*` (after Postgres) [auto:S]
- [ ] Add published events (`events` with status 'published') as an `events.csv.gz` dump [auto:S]
- [ ] Parquet dumps once `pyarrow` is an approved dependency [auto:S]
- [ ] Show the dumps' "as of" date and file sizes on `/data` from `manifest.json` [auto:S]

