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
- [ ] Place picker + "my place now" basic view: new `/place` route and template; status lines per medium from `/api/query`; "what changed" from the same. [auto:M]
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
- [ ] Data centers on the site: a `data_center` site kind (same site page as the rest) and a layer; on the basic place
      view a plain card when one is near ("what we know / what's not disclosed" for water, energy, noise and purpose,
      each with its source), and on the advanced view the full record and filings. Where the data doesn't exist,
      say so and say how people can ask (PSC dockets, DEP permit comments, public records requests) [auto:M]
- [ ] Roadmap: a "Data centers" section (water, energy, noise, purpose, what's undisclosed) and a later
      "Noise and light pollution" medium (night-sky brightness from satellite data, community noise reports and
      sensors) in ROADMAP.md, placed after Land [auto:S]
- [ ] Nav collapse + terms pass: six-item nav, "More" menu, glossary file, rename Sources→Facilities, emoji removed, Admin to footer. [auto:S]
- [ ] Map bounds + y-axis + default sensor: items from §6. [auto:S]
- [ ] /sources pagination + /water heading fix + haze opt-in. [auto:S]
- [ ] API caching, gzip, bbox for heavy layers, self-hosted libs. [auto:M]
- [ ] Accessibility pass: alt/aria/focus on custom controls, reduced-motion, contrast check in the harness. [auto:M]
- [ ] Explore v1 (query builder UI): form over `/api/query`, table + CSV + API link. [auto:M]
- [ ] (session with Corey) Postgres + Timescale migration — session with Corey, L: steps 1–6 from ROADMAP; touches prod. SQLite-ism fixes beforehand are.
- [ ] (session with Corey) Read-only SQL role + `/explore/sql` — session (needs Postgres), M.
- [ ] Nightly public dumps + rate limit + CORS after Postgres; S on SQLite for the dumps alone. [auto:S]
- [ ] Place-based alerts, any medium: subscriptions keyed to place + rule; spill/violation triggers; SMTP go-live is a session item. [auto:M]
- [ ] (session with Corey) Land medium ingest (Superfund/RCRA/AML) — session S to fetch (network), then to model and surface.
- [ ] Neighbor cross-check + auto event candidates. [auto:M]
- [ ] (session with Corey) Demo script + partner walkthrough S with Corey: the five-step narrative from §4, one URL per step.
### From job proposals
- [ ] Add a glossary file (`docs/GLOSSARY.md`) with the six terms (sensor, monitor, gauge, sample site, facility, data source), so the nav and terms pass can link to it [auto:S]
- [ ] Move `/api/areas` and `/api/water/near` onto `/api/query` (add an area filter and a time window for measurements), with snapshot tests like `/api/near`'s [auto:M]
- [ ] A small sites table for AirNow reference monitors and readings-table sensors, filled at ingest, so they join the `site` view without a GROUP BY over `readings` [auto:S]
- [ ] Remove the exact-duplicate rows from `abandoned_wells.json` in its fetch script (this changes the Nearby list, so Corey decides) [auto:S]
- [ ] An `/api/query` series mode: `site_id` + `param` + `from`/`to` returns measurement rows plus a CSV link, as the base for Explore v1 [auto:M]
- [ ] Re-queue "Depth toggle + advanced view" once the Place picker branch (`czjob/6d558c`) is merged into `corey/auto`. [auto:M]
- [ ] Data centers, primary records: pull DEP DAQ engineering evaluations for R14-0042, R13-3713/3714/3715 and DEP water-use (large-quantity user) reports; fill the "none found" water and generator fields in `data_centers.json`. [auto]
- [ ] Data centers, PSC: search the PSC case system for each operator and confirm the 24-0611-E-T-PW final order; add case links to `dockets`. [auto]
- [ ] Data centers, watch list: a dated "open comment periods / hearings" list in DATA-CENTERS.md (e.g. R14-0042 comments due 2026-11-13), refreshed from DEP notices. [auto]
- [ ] Data centers, geocode: add verified coordinates (from permits or parcel records) for sites that have a street address, with `location_precision` filled. [auto]

