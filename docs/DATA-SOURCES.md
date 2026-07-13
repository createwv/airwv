# Data sources — a field guide to public environmental data

AirWV is stitched together from **public, mostly keyless** government and open data.
Half the value of this project is the *map of where that data lives and how to tap
it* — so this doc is written to be **learned from and copied**, not just referenced.
If you're building your own community-environmental tool (in WV or anywhere), start
here: almost everything below is a national or open dataset with a state filter.

**Principles we follow (and recommend):**

1. **Go direct to the primary source.** Aggregators are great for discovery, but the
   agency's own feed is what you can't get rate-limited or de-platformed off of.
2. **Prefer keyless bulk files / open REST.** Every source here except PurpleAir needs
   no account. When a source offers a whole-state file, take the file over per-record
   API calls.
3. **Carry provenance end-to-end.** Every data file records its `source`,
   `fetched_at`, `scope`, and a `disclaimer`; every API response passes those through;
   the UI shows them; the public [`/data`](../src/airwv/web/templates/data.html) page
   catalogs them. A number with no source is a rumor.
4. **State the limits honestly.** Preliminary vs. QA'd, "reported" vs. "confirmed,"
   approximate vs. exact location — say so where the user sees it.

---

## The catalog at a glance

| Layer | Source | Access | Key? | Our fetcher → endpoint |
|---|---|---|---|---|
| Community air sensors | **PurpleAir** | REST | **key + points** | `ingest collect`/`backfill` → `/api/sensors` |
| Live reference air | **EPA AirNow** | bulk hourly file | no | `ingest airnow` → `/api/sensors` |
| Historical reference air | **EPA AirData (AQS)** | annual bulk file | no | `ingest airdata` → validation |
| Real-time water gauges | **USGS NWIS** | REST JSON | no | `ingest water` → `/api/water/*` |
| Water lab/grab samples | **EPA Water Quality Portal** | REST CSV | no | `fetch_water_samples.py` → `/api/water/near` |
| Facility compliance | **EPA ECHO** (`echo_rest`) | REST (qid paging) | no | `fetch_facility_status.py` → `/api/facilities` |
| NPDES water dischargers | **EPA ECHO** (`cwa_rest`) | REST | no | `fetch_water_sources.py` → `/api/sources` |
| Drinking-water systems | **EPA ECHO / SDWIS** (`sdw_rest`) | REST | no | `fetch_sdwa.py` → `/api/sdwa` |
| Toxic-release facilities | **EPA Envirofacts (TRI)** | REST | no | `fetch_sources.py` → `/api/sources` |
| O&G permits / wells / mining / coal NPDES / 303(d) | **WV DEP TAGIS ArcGIS** | ArcGIS REST | no | `fetch_dep_*`, `fetch_coal_npdes.py`, `fetch_abandoned_wells.py` |
| Reported oil/chemical spills | **National Response Center (USCG)** | annual `.xlsx` | no | `fetch_nrc_spills.py` → `/api/nrc-spills` |
| Building footprints (near-homes) | **Microsoft US Buildings** | GeoJSON | no | `enrich_wells_proximity.py` |
| Wind / weather | **Iowa State Mesonet (ASOS)** | CSV | no | `fetch_wind.py` → `/api/wind-roses` |

All fetchers live in [`scripts/`](../scripts/); each has a docstring with its source
URL, quirks, and how to run it. Committed outputs land in
[`src/airwv/data/`](../src/airwv/data/) and are served read-only by the API.

---

## Access patterns you'll reuse

Most of the "how do I connect to X" work reduces to five patterns. Learn these and
almost any government dataset opens up.

### 1. Keyless bulk files (the best case)
Some agencies publish the *whole dataset* as a file. No key, no quota, no per-record
paging — one download has everything and you filter locally.
- **EPA AirNow** `HourlyAQObs` (every US monitor, hourly): <https://files.airnowtech.org>
- **EPA AirData/AQS** annual daily files: <https://aqs.epa.gov/aqsweb/airdata/download_files.html>
- **NRC spills** annual workbooks: `https://nrc.uscg.mil/FOIAFiles/CY{yy}.xlsx`
- **Microsoft Buildings** per-state GeoJSON (see §Geospatial).

> **Lesson:** if a source offers both an API *and* a bulk file, the bulk file is
> almost always the right call for backfill/analysis. It can't be rate-limited.

### 2. Open REST with a state filter
Point queries with a `state=WV`-style parameter, returning JSON or CSV.
- **USGS NWIS** instantaneous values (JSON): `waterservices.usgs.gov/nwis/iv`
- **EPA Water Quality Portal** (CSV): `waterqualitydata.us/data/Result/search` with
  `statecode=US:54` + `characteristicName=`
- **EPA Envirofacts** (TRI): `data.epa.gov/efservice/tri_facility/state_abbr/WV/JSON`

### 3. EPA ECHO's two-step "qid" pattern (and its column-split gotcha)
ECHO (`echodata.epa.gov/echo/*_rest_services`) is the single richest compliance
source — air, water, drinking water, enforcement. Its REST services work in two
steps: **`get_*` returns a query id (`QueryID`)**, then **`get_qid?qid=…&pageno=N`**
pages the records.
- **Gotcha we hit:** the JSON `get_qid` and the CSV `get_download` return *different
  column sets*. For facilities we needed compliance fields **and** longitude, but
  `get_qid` has `FacLat` (no lon) while the CSV has `FacLong` (no compliance) — so we
  **join the two on `RegistryID`**. See `fetch_facility_status.py`.
- SDWA (`sdw_rest_services.get_systems`) conveniently returns all ~4,400 WV systems in
  a single page under `WaterSystems`. Not all services page the same way — check.

### 4. ArcGIS REST (state GIS servers)
State agencies expose GIS layers via ArcGIS REST — this is how we get WV DEP's oil &
gas, mining, coal NPDES, and 303(d) impaired streams. The root
`https://tagis.dep.wv.gov/arcgis/rest/services?f=json` lists folders → services →
layers. Each layer's `/query` endpoint takes SQL-ish `where=`, `outFields=*`,
`returnGeometry`, `outSR=4326`, and (crucially) **`resultOffset`/`resultRecordCount`
for paging** because a layer caps a page at its `maxRecordCount` (3,000 for the wells
layer). Two big wins here:
- **Spatial queries server-side.** We flag coal dischargers on impaired streams by
  sending each permit's outlet **envelope** to the 303(d) layer's `/query`
  (`geometryType=esriGeometryEnvelope`, `spatialRel=esriSpatialRelIntersects`) — no
  local geometry library needed (`fetch_coal_npdes.py`).
- **`outStatistics` + `groupByFieldsForStatistics`** for server-side counts (we used
  it to size the abandoned-well and status distributions before pulling records).

### 5. Placing records that only have a county
Lots of government data carries a **county name/FIPS but no coordinates** (NRC spills,
SDWA systems). We place those at the **county centroid**, clearly flagged as
approximate. The shared lookup is [`src/airwv/wvgeo.py`](../src/airwv/wvgeo.py)
(`WV_COUNTY_FIPS`, `WV_COUNTY_CENTROID`, `canon_county` — which fixes `str.title()`
turning "McDowell" into "Mcdowell").

---

## Air

### PurpleAir — community sensors (the only keyed source)
Low-cost community sensors; the network WVCAG deploys. Needs an API **read key** and
**points** (a usage budget; we have a grant — see [PURPLEAIR-POINTS.md](PURPLEAIR-POINTS.md)).
We resolve device names → `sensor_index`, then pull current + historical readings.
`ingest collect --light` uses a lean field set for a cheap ongoing timer.
- Correction: PurpleAir reads high; we apply the **EPA/Barkjohn (2021)** correction
  and validate against reference monitors (`/api/validate`).
- VOC is a **relative gas-response index**, not a concentration, and can't be
  calibrated (no reference VOC). The UI says so everywhere it appears.

### EPA AirNow — live reference (keyless)
EPA's real-time program (EPA + state/local/tribal agencies). The hourly `HourlyAQObs`
file at <https://files.airnowtech.org> is **keyless, no quota** — one download has
every US monitor; we keep WV + border. Preliminary (not QA'd), for public awareness.
`ingest airnow`, hourly via `airwv-airnow.timer`.

### EPA AirData / AQS — deep history (keyless)
Pre-generated **annual daily** files (`daily_88101_YYYY.zip` = PM2.5, `44201` = ozone)
at <https://aqs.epa.gov/aqsweb/airdata/>. Fully QA'd regulatory data back to the
2000s. `ingest airdata --start-year 2007`. (Some years use `M/D/YYYY` dates — the
parser handles both.)

> **OpenAQ.** The open
> **[OpenAQ](https://openaq.org)** project is how we learned to reach EPA's *own* air
> data: poking their API showed US real-time data is tagged `provider="AirNow"` — i.e.
> they re-serve EPA's AirNow feed — which pointed us at the primary EPA sources we now
> use directly. **Our air reference is EPA end-to-end and stores no OpenAQ data:** every
> reference row is `source='airnow'` or `'epa_airdata'`, traceable to EPA.
>
> *The current-year wrinkle (worth understanding):* EPA's **finalized** history (the
> AirData annual files) isn't published *retroactively* for the current year — the 2026
> file lands ~2027. OpenAQ's platform **does** retain current-year observations in
> queryable form, but their terms authorize **light, real-time querying within limits
> (~60/min, ~2,000/hr, with attribution) — not bulk download.** So we don't pull history
> from them; instead we cover the current year with EPA's own **AirNow**, whose hourly
> files are *retained* and keyless (`ingest airnow --backfill-days N`). OpenAQ stays
> worth keeping in mind for what EPA's US-only feeds can't do — non-US / global data,
> networks outside the US regulatory set, redundancy — used the way they intend.

---

## Water

### USGS NWIS — real-time gauges (keyless)
Instantaneous-values JSON API (`waterservices.usgs.gov/nwis/iv`) for flow, gage
height, and some in-situ pH/DO/conductance/turbidity. `ingest water`, on a timer.

### EPA Water Quality Portal — lab & grab samples (keyless)
`waterqualitydata.us` federates EPA + USGS + state monitoring into one CSV API. We
pull WV (`statecode=US:54`) by characteristic (iron, selenium, sulfate, pH,
conductance, E. coli, …) in `fetch_water_samples.py`. **Add a parameter** by adding a
`CharacteristicName → (key, unit)` row to `CHARS` and running `--chars <Name>` (that's
how selenium got added). Periodic samples, not live — each site shows its latest date.
Served via `/api/water/near` (the reusable "measured water near a point" join used by
coal dischargers *and* facilities).

### WV DEP Coal NPDES + 303(d) impaired streams (ArcGIS)
`fetch_coal_npdes.py` pulls active coal-mine discharge outlets from WV DEP's
`mining_reclamation` ArcGIS layer, aggregates to the permit, and **spatially joins**
each permit's outlet envelope to the `watershed_assessment` **2016 303(d)** layer to
flag impaired streams + causes — a good worked example of pattern §4.

---

## Facilities, permits & compliance

### EPA ECHO — the compliance backbone (keyless)
One source, several services under `echodata.epa.gov/echo/`:
- `echo_rest_services` → **major facility compliance** (violations, SNC, programs) —
  `fetch_facility_status.py` → `/api/facilities` (the ⚖️ dashboard layer + Sources).
- `cwa_rest_services` → **NPDES water dischargers** — `fetch_water_sources.py`.
- `sdw_rest_services` → **Safe Drinking Water Act systems** (below).
Every facility links back to its ECHO Detailed Facility Report / effluent charts.

### EPA SDWIS via ECHO — drinking-water systems (keyless)
`sdw_rest_services.get_systems?p_st=WV` → 4,400 public water systems with
health-based-violation flags, population served, source type, lead/copper status.
`fetch_sdwa.py` → `/api/sdwa` (Water page county map + system table). This is the
dataset that represents the **Wyoming County drinking-water crisis** the ambient
feeds can't show.

### EPA Envirofacts — TRI toxic-release facilities (keyless)
`data.epa.gov/efservice/tri_facility/state_abbr/WV/JSON` — the curated pollution
sources on the Sources page (`fetch_sources.py` → `sources.json` → `/api/sources`).

### WV DEP oil & gas permits + mining permits (ArcGIS)
`fetch_dep_permits.py` (O&G permit pipeline: requested/approved/under-construction) and
`fetch_dep_mining.py` (coal/mineral mining lifecycle + disturbed/reclaimed acreage),
both from `tagis.dep.wv.gov/.../WVDEP_enterprise/{oil_gas,mining_reclamation}`. See
[echo-facilities memory / ROADMAP Phase 5] for the lifecycle mapping.

---

## Wells, spills & the "near me" synthesis

### WV DEP abandoned & orphan wells (ArcGIS)
`fetch_abandoned_wells.py` — 15,455 abandoned wells, 4,721 orphans (no operator = the
state's to plug). `enrich_wells_proximity.py` then tags each with distance to the
nearest **Microsoft building footprint** (§Geospatial) for the near-homes risk flag.

### National Response Center — reported spills (keyless)
The USCG spill hotline publishes annual `.xlsx` workbooks (`nrc.uscg.mil/FOIAFiles/`)
— relational sheets keyed on `SEQNOS`. `fetch_nrc_spills.py` keeps WV, flattens
incident + material + details + caller, converts DMS coords (or county-centroids the
~80% without coords). The public record of "a spill was reported here," even when the
follow-up sampling never becomes public. → `/api/nrc-spills`, Events page.

### `/api/near` — the reverse guide
`/api/near?lat=&lon=&km=` aggregates **all** of the above within a radius into one
categorized list (gas/air/water/chemical/sensor). It's the clearest example of the
payoff: once every source carries coordinates + provenance, joining them by location
is easy. Powers the **What's near me?** page.

---

## Geospatial helpers (no PostGIS/shapely required)

We deliberately avoid a heavy geo stack so the project stays easy to run:
- **Haversine + a dict-grid index** for nearest-neighbour. `enrich_wells_proximity.py`
  streams Microsoft's **1.05M-building** WV GeoJSON
  (`minedbuildings.z5.web.core.windows.net/legacy/usbuildings-v2/WestVirginia.geojson.zip`,
  line-delimited so it streams), grids first-vertices, and finds each well's nearest
  building in ~3s. The 273 MB source is cached in `/tmp`, **never committed** — only
  the small enriched result is.
- **Server-side ArcGIS spatial queries** (pattern §4) for point-in/near-line joins.
- **County centroids** (`wvgeo.py`) for county-only records.

---

## How provenance shows up in the app (front + back end)

- **Data files** (`src/airwv/data/*.json`) each carry `source`, `scope`, `disclaimer`,
  `fetched_at`.
- **API responses** pass those through (`/api/facilities`, `/api/sdwa`,
  `/api/nrc-spills`, `/api/dep-*`, `/api/coal-npdes`, `/api/abandoned-wells`, …).
- **The UI** shows the source + "as of" date on every layer's section, and links each
  record back to its authoritative page (ECHO report, DEP well record, NRC).
- **[`/data`](../src/airwv/web/templates/data.html)** is a public catalog page listing
  every layer, its source, freshness, and record count (from `/api/data-catalog`).

---

## Adding a new source — the recipe

1. **Find the primary source** (agency > aggregator). Prefer a keyless bulk file or
   open REST; check for a `state=`/FIPS filter.
2. **Probe it** with `curl … | python -m json.tool` (or `?f=json` for ArcGIS) to learn
   fields, paging, and quirks *before* writing the fetcher.
3. **Write `scripts/fetch_<x>.py`** with a docstring that records the URL, quirks, and
   run command. Emit `source`/`scope`/`disclaimer`/`fetched_at` + the records to
   `src/airwv/data/<x>.json`. Place county-only records via `wvgeo`.
4. **Serve it** with a small read-only `/api/<x>` endpoint that passes provenance
   through and supports the filters the UI needs.
5. **Surface it** — a UI section/layer that shows the source + date and links records
   to their authoritative record; add a row to `/api/data-catalog`.
6. **Document it** — a row in the table above + a short section here, and a line in the
   memory notes if there's a non-obvious gotcha.

---

## Attribution

Data courtesy of: **US EPA** (AirNow/AirData/ECHO/SDWIS/Envirofacts — AirNow is
preliminary, AQS finalized; not for regulatory decisions), **USGS** (NWIS + Water
Quality Portal), **WV DEP** (TAGIS GIS: oil & gas, mining, coal NPDES, 303(d)),
**US Coast Guard / National Response Center** (spill reports), **Microsoft** (US
Building Footprints, ODbL), **Iowa State Mesonet** (ASOS wind), **PurpleAir**
(community sensors), and **[OpenAQ](https://openaq.org)** (whose open platform helped
us find the primary EPA feeds). Community sensor deployment led by **WVCAG** with
**Create WV** and partners.
