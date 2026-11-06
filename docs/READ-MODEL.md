# Read model: `site`, `measurement`, `record`

The first step of the six-noun model in [DIRECTION.md](DIRECTION.md) §1: SQL views over the
tables and JSON layers we already have. Code: `src/airwv/storage/readmodel.py`
(`store.read_model`). Endpoints on it: `/api/near` (unchanged output) and `/api/query`
(parameters in [DATA-SOURCES.md](DATA-SOURCES.md#apiquery--one-read-path-over-the-unified-views)),
which the `/place` basic view reads for every line it shows.

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

## Known gaps

- `site_id` repeats where a source file repeats a row verbatim (about 1,300 abandoned-well
  ids) and for oil & gas permits, where one API number has several applications.
  `/api/near` has always listed the duplicate wells; deduping them is a visible change.
- AirNow reference monitors in `readings` aren't sites yet (only `reference_monitors.json`);
  a GROUP BY over the readings table per query is too slow, so that needs a small sites table.
- `/api/areas` and `/api/water/near` aren't moved onto `/api/query` yet.
- "What changed" (`since`) only sees records with a date (`effective`): spills and permit
  issue dates. Violations and compliance status carry no date in the source files, so a
  *new* violation can't be told apart from an old one yet; community reports and events
  aren't in the views.
