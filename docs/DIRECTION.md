# Direction spec — one place, two depths, one model

*Approved by Corey 2026-11-05, from the direction review (`airwv-direction-review-2026-11-05.md`,
kept outside the repo). This page is the contract later work builds on; the plan items in
`plans/autopilot.md` implement it. It supersedes the "modes" in [FRONTEND.md](FRONTEND.md) and
reframes ROADMAP Phase 8's "Analysis / map redesign".*

## Why

Today the site has eleven top-level doors of three kinds (media, record types, tasks), the same
object on six pages with no home URL, three shapes of "reading" table, drifting words, and every
page opening statewide. People have to do the join in their head. The fix: join on the **place**
and the **object**, and show the same answer at two depths.

## 1. Data model: six nouns

| Noun | What it is |
|---|---|
| `place` | a point or area a person cares about: lat/lon + radius, town, county, region |
| `site` | a thing that produces data or carries a record, with a location, a `kind` and a `medium` (air / water / land). Kinds: `sensor`, `reference_monitor`, `gauge`, `sample_site`, `facility`, `well`, `permit`, `water_system`, `spill_report`, `cleanup_site`, `data_center` |
| `measurement` | one value, tall: `site_id, ts, parameter, value, unit, quality, source, raw`, with a parameter catalog (`pm2_5`, `voc`, `ozone`, `ph`, `conductance`, `selenium`, …) |
| `record` | a non-measurement state about a site: violation, permit stage, 303(d) listing, orphan status, compliance status; effective dates + public-record URL |
| `event` | a narrated episode: time window, places/sites involved, cause, scope |
| `rule` | what good/bad means for a parameter: EPA AQI bands, aquatic-life bands, MCLs, our trend/anomaly thresholds; versioned and cited |

Every row carries **provenance** (source, fetched_at, scope, disclaimer: the existing JSON
convention). All human input uses one **trust** enum: `regulatory | community_sensor |
field_verified | community_unverified`.

This is a reframing, not a rewrite. First step is **SQL views** over what exists: `readings` →
measurements with `parameter='pm2_5'` etc.; `water_readings` already fits; `data/*.json` layers →
`site` + `record`; `sources_enrich.merge_sources` is the site-identity step. One read endpoint,
`GET /api/query`, generalizes `/api/near`, `/api/areas`, `/api/water/near`; `/api/near` is
reimplemented on the views with a snapshot test proving identical output.

## 2. Views: Basic and Advanced over the same objects

**Basic (default for new visitors), place-first:**
- *My place now*: one status line per medium in plain words: band color, number, time, distance
  ("Air: moderate, 15 µg/m³, as of 1:00 PM, nearest sensor 2 mi"; "Water: no live gauge within
  10 mi; latest samples Jul 12"). Nothing else.
- *What's near me*: today's `/nearby` list, grouped by medium, worst first, symptom matcher kept.
- *What changed (7 days)*: band crossings, new spills, new violations, new reports, events here.
- *Watch this place* (the `/alerts` form, pre-filled) and *Report* (existing modal, pre-located).

**Advanced:** the same place with time range, parameter, layer tree, cross-site comparison,
series / time-of-day / day-night, validation vs reference, wind weighting, exports, and the API
link for what's on screen. Plus **site pages** and **Explore** (below).

**Moving between them:**
- One **depth toggle** `[Basic | Advanced]` in the header; remembered in localStorage and the URL
  (`view=adv`). Switching keeps place, time and parameter; it only changes how much is shown. It
  is the only way into Advanced, so nobody meets the layer tree by accident.
- **"Show the data behind this"** on every basic element: status line → site page, band → rule,
  time → series with that window.
- **State lives in the URL**, read and written by every map, chart and list (one shared
  `state.js`): `/place?lat=&lon=&km=&medium=air&param=pm2_5&from=&to=&view=adv&layers=sensors,ref,wells`.
  A shared URL reproduces the screen.

## 3. Routes and navigation

```
Header: [logo]  Place ▾ (town, radius)  [Basic | Advanced]  Air · Water · Land  Alerts  More ▾

/                  my place (basic), or statewide if no place is set
/place?…           same, with state in the URL
/site/<kind>/<id>  the single home of one object: readings, records, nearby, events,
                   provenance, "report here", "watch this"
/explore           advanced only: query builder, exports, API docs
/air               redirects to /place?…&view=adv
/events, /spills   kept (spills are a site kind; the page is a filtered list)
/learn, /data, /about   unchanged, under "More"; /admin moves to the footer
```

- Nav goes from 11 items to **≤ 6**: place, medium, depth, alerts, More (Events, Spills, Learn,
  Data, About). No emoji in nav labels.
- `/sources` becomes **Facilities**, reached by place or search (paginated), not a 505-card browse.

**Terms (one usage, kept in a glossary file):** *sensor* = community device; *monitor* = EPA
reference monitor; *gauge* = USGS; *sample site* = WQP; *facility* = a regulated place with
records; *data source* = where we got the data (never "source" alone).

## 4. The demo slice (what "done" looks like first)

One place, both depths, three media, one query path. **Demo place: Rutledge (Charleston), 5 mi.**
Known places in the picker: Charleston / Kanawha Valley, Parkersburg, Fairmont, Wyoming County;
radius 5 / 10 / 25 mi; or geolocate / type a town.

Narrative (one URL per step): pick Rutledge → the basic screen tells the story in about six lines
→ "show the data behind this" → advanced view with VOC and wells → site page for an orphan well →
Explore: "every community sensor-hour over 35 since June" → CSV.

Shown: place picker; basic (air + water + land status, near me, what changed, watch, report);
advanced (time, parameter, layers, series/diurnal, sites-in-range table, CSV + API link); site
pages for sensors, monitors, gauges, facilities, water systems, wells; Explore v1 over
measurements and records; Events, Spills, Learn, Data, About as they are.

Demoted, still reachable from Advanced → "more analysis": statewide layer tree as a landing page,
ozone-as-metric, wind weighting, validation panel, well backlog, "around a polluter". Haze overlay
defaults off. Land shows only abandoned/orphan wells and "no cleanup-site data yet" until the Land
ingest lands.

## 5. Querying, in build order

1. **Saved filters** (everyone): the URL is the filter; "copy link", "save this view" (localStorage).
2. **Query builder** (`/explore`): *show [measurements | records | sites | events] of [parameter /
   kind] at [place + radius | region | county | site] between [dates] where [value / status]
   grouped by [site | day | county]* → table, map, chart, CSV and the equivalent `/api/query` URL.
   Server-side caps: rows, time span, max radius.
3. **Read-only SQL** (`/explore/sql`): only after Postgres; public views only, `statement_timeout`,
   row cap, no PII columns, queries logged. Session with Corey.
4. **Bulk**: nightly CSV/Parquet dumps of public tables + documented API with CORS and rate limit.

Safety everywhere: public views only, caps, timeouts, no PII (`reports.contact_*`, `ip_hash`,
exact report coordinates), logged queries.

## 6. Acceptance (the product harness checks these)

- On a 390 px phone, `/` or `/place` shows a reading above the fold.
- Nav has ≤ 6 top-level items.
- Every map's first paint is bounded to WV (or to the place's radius), never five states.
- A shared `/place?…` URL reproduces the same screen, in both depths.
- `/api/near` output is unchanged after it moves onto the views (snapshot test).
- Every number shown links to its site page, rule or `/data` entry, with its time.

## Open for Corey

- Header wording and look of the toggle (labels "Basic / Advanced" are from the review; the
  mockup also shows "Adv" on phone).
- Whether `/` shows a default place (e.g. Rutledge) for visitors with no place set, or statewide
  as the review says. This spec keeps statewide.
