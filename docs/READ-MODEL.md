# Read model: `site`, `measurement`, `record`

The first step of the six-noun model in [DIRECTION.md](DIRECTION.md) §1: SQL views over the
tables and JSON layers we already have. Code: `src/airwv/storage/readmodel.py`
(`store.read_model`). Endpoints on it: `/api/near`, `/api/areas` (+ `/series`) and
`/api/water/near` (each unchanged, pinned by a snapshot in `tests/snapshots/`) and `/api/query`
(parameters in [DATA-SOURCES.md](DATA-SOURCES.md#apiquery--one-read-path-over-the-unified-views)),
which the `/place` basic view reads for every line it shows and the `/explore` query
builder puts in a table (with `format=csv` for the download). Its series mode
(`site_id` + `param` + `from`/`to`) reads one site's `measurement` rows, for charts: the
site pages `/site/<kind>/<ref>` for `sensor`, `reference_monitor`, `gauge` and `sample_site`
chart the last 7 days (monitors 30, sample sites 365) up to the site's latest reading from
it, with the same URL as their CSV and API links (`?param=` picks what to chart; the
choices come from `ReadModel.parameters`). Monitors match their AirData history
(`54-003-0003`); AirNow's hourly rows use a 9-digit AQSID and aren't on the page yet.
`/site/permit/<ref>` (coal discharge, DEP mining, oil & gas) reads one `site` row and its
`record` rows: the `permit_stage` (DEP mining, oil & gas) and, for coal discharges, the
`impaired_303d` causes, with the layer's disclaimer; `?view=adv` lists every field.
Every site page (data centers too) also shows "Related nearby": `site.js` asks `/api/query`
for the nearest site of each other kind (`kind=<k>&limit=1`, 25 mi) and lists the 5 closest,
linking each to its site page or, for kinds without one yet, to its own record (`url`).

## How it works

- The views are **TEMP** (per database connection), so nothing is written to the database
  file. The `data/*.json` layers are loaded into a temp `layer_doc` table and read with
  `json_each`; a layer reloads when its file's mtime changes (the collectors rewrite them).
- Distances use a `hav_mi(lat1, lon1, lat2, lon2)` SQL function registered per connection
  (the same haversine the app always used, so results match to the bit).
- SQLite only. The Postgres migration ports these to real views over `jsonb`.

## Views

| View | Columns | From |
|---|---|---|
| `site` | `site_id` (`kind:ref`), `kind`, `medium`, `layer`, `ref`, `name`, `lat`, `lon`, `county`, `url`, `ord`, `attrs` (the source row as JSON) | every JSON layer; `water_readings` (one site per id, placed at its latest reading); the resolved community-sensor listing |
| `measurement` | `medium`, `source`, `ref`, `ts`, `parameter`, `value`, `unit`, `quality`, `raw` | `readings` (one row per non-null air field: pm1_0, pm2_5, pm10, aqi, voc, ozone, temperature, humidity, pressure) and `water_readings` |
| `record` | `site_id`, `layer`, `kind`, `value`, `effective`, `url`, `as_of` | orphan wells, ECHO violation + compliance status, coal 303(d) listing, mining / oil-and-gas permit stage, NRC spill reports, SDWA health-based violations, data-center project status |

Site kinds today: `well`, `facility` (ECHO + TRI/curated sources), `permit` (coal NPDES,
DEP mining, DEP oil & gas), `spill_report`, `water_system`, `reference_monitor`, `sensor`,
`gauge` (USGS), `sample_site` (WQP), `data_center` (`data_centers.json`, medium `land`; most
have no public coordinates, so `lat`/`lon` are NULL and only county finds them; one
`project_status` record each). Measurements join sites on (`medium`, `ref`).

Place-based record alerts (`alerts.RECORD_RULES`, `ReadModel.alert_records`) read the
`record` view joined to `site`: spill reports, SDWA health-based violations and ECHO
violations. A record is "new" when its `site_id|kind|value` key wasn't there at the last
check, so a status or contaminant change counts as new too.

## Known gaps

- `site_id` repeats for oil & gas permits, where one API number has several applications.
  (The abandoned-well layer's verbatim repeats are dropped by `fetch_abandoned_wells.py`.)
- AirNow reference monitors in `readings` aren't sites yet (only `reference_monitors.json`);
  a GROUP BY over the readings table per query is too slow, so that needs a small sites table.
- `/api/water/near` now places each site at its latest reading (the `site` view), not at
  whichever latest-per-parameter row came first; same in the fixture, may move a drifting
  DEP site a few metres.
- "What changed" (`since`) only sees records with a date (`effective`): spills and permit
  issue dates. Violations and compliance status carry no date in the source files, so a
  *new* violation can't be told apart from an old one yet; community reports and events
  aren't in the views.
