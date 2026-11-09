"""The unified read model: three SQL views over what already exists.

* ``site``        — anything with a location that produces data or carries a record:
                    the ``data/*.json`` layers, the water sites in ``water_readings``, the
                    community sensors the app resolved from the public listing and the
                    sensors / reference monitors in ``readings`` (via ``reading_sites``).
* ``measurement`` — one value, tall: ``readings`` (one row per non-null air field) and
                    ``water_readings`` (already tall).
* ``record``      — non-measurement state about a site: violations, compliance status,
                    303(d) listings, orphan status, permit stage, spill reports.

See docs/DIRECTION.md §1 and docs/READ-MODEL.md. The views are TEMP (per connection),
so nothing is written to the database file: the JSON layers are loaded into a temp
``layer_doc`` table (reloaded when a file's mtime changes) and read with ``json_each``.
SQLite only for now; the Postgres migration ports these to jsonb / real views.
"""

from __future__ import annotations

import json
import math
from contextlib import contextmanager
from pathlib import Path

from airwv.wvgeo import county_names

DATA_DIR = Path(__file__).parent.parent / "data"

# layer name -> JSON file in DATA_DIR (each keeps its provenance block in the file)
FILE_LAYERS = {
    "abandoned_wells": "abandoned_wells.json",
    "echo_facilities": "echo_facilities.json",
    "sources": "sources.json",
    "coal_npdes": "coal_npdes.json",
    "dep_mining": "dep_mining.json",
    "dep_permits": "dep_permits.json",
    "nrc_spills": "nrc_spills.json",
    "sdwa_systems": "sdwa_systems.json",
    "reference_monitors": "reference_monitors.json",
    "data_centers": "data_centers.json",
}

# air fields of ``readings`` exposed as measurements: (column, unit)
AIR_PARAMETERS = (
    ("pm1_0", "µg/m³"), ("pm2_5", "µg/m³"), ("pm10", "µg/m³"), ("aqi", "AQI"),
    ("voc", "index"), ("ozone", "ppb"), ("temperature", "°F"), ("humidity", "%"),
    ("pressure", "hPa"),
)


def hav_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle miles; registered in SQL as ``hav_mi`` (same math as the app's).
    NULL in, NULL out: SQLite may call it before ``lat IS NOT NULL`` filters a row."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dp / 2) ** 2 + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2)) * math.sin(dl / 2) ** 2)
    return 2 * 3959 * math.asin(math.sqrt(h))


def in_county(raw: str | None, wanted: str) -> bool:
    """Does a ``county`` field name one of ``wanted`` (comma-joined canonical names)?
    Registered in SQL; the layers spell counties several ways (see ``county_names``)."""
    return any(n in wanted.split(",") for n in county_names(raw))


def _j(path: str) -> str:
    return f"json_extract(j.value, '$.{path}')"


def _layer_branch(layer: str, array: str, kind: str, medium: str, ref: str, name: str,
                  county: str = "NULL", url: str = "NULL") -> str:
    return f"""
    SELECT '{kind}:' || {ref} AS site_id, '{kind}' AS kind, {medium} AS medium,
           '{layer}' AS layer, {ref} AS ref, {name} AS name,
           {_j('lat')} AS lat, {_j('lon')} AS lon, {county} AS county, {url} AS url,
           j.key AS ord, j.value AS attrs
    FROM layer_doc d, json_each(d.doc, '$.{array}') j WHERE d.layer = '{layer}'"""


_ECHO_WATER = ("(EXISTS (SELECT 1 FROM json_each(j.value, '$.programs') p WHERE p.value IN ('water', 'drinking-water'))"
               " AND NOT EXISTS (SELECT 1 FROM json_each(j.value, '$.programs') p WHERE p.value = 'air'))")

# `readings` sources that are EPA reference monitors (the rest are community sensors)
_REFERENCE_SOURCES = "'airnow', 'epa_airdata'"

# AirNow ids are 9-digit AQSIDs (540390020); reference_monitors.json and AirData use the
# dashed AQS form (54-039-0020). Normalise to the dashed form so one monitor is one site.
_AQS_ID = ("CASE WHEN sensor_id GLOB '" + "[0-9]" * 9 + "*' THEN substr(sensor_id, 1, 2) || '-' || "
           "substr(sensor_id, 3, 3) || '-' || substr(sensor_id, 6, 4) ELSE sensor_id END")

SITE_VIEW = "CREATE TEMP VIEW site AS" + " UNION ALL".join([
    _layer_branch("abandoned_wells", "wells", "well", "'land'", _j("id"), "NULL", _j("county"),
                  "'http://www.wvgs.wvnet.edu/oginfo/pipeline/pipeline2.asp?txtsearchapi=47' || "
                  f"replace(coalesce({_j('id')}, ''), '-', '')"),
    _layer_branch("echo_facilities", "facilities", "facility",
                  f"CASE WHEN {_ECHO_WATER} THEN 'water' ELSE 'air' END",
                  _j("registry_id"), _j("name"), _j("county"), _j("echo_url")),
    _layer_branch("sources", "sources", "facility", "'air'", "'tri-' || j.key", _j("name"),
                  url=f"'/sources#facility=' || coalesce({_j('name')}, '')"),
    _layer_branch("coal_npdes", "permits", "permit", "'water'", _j("permit"),
                  "'Coal discharge — ' || " + _j("operator"), url=_j("effluent_url")),
    _layer_branch("dep_mining", "mines", "permit", "'land'", _j("permit_id"),
                  f"coalesce({_j('facility')}, {_j('operator')})"),
    _layer_branch("dep_permits", "permits", "permit", "'land'", _j("permit_id"), _j("operator"),
                  _j("county"), _j("link")),
    _layer_branch("nrc_spills", "spills", "spill_report",
                  f"CASE WHEN {_j('reached_water')} THEN 'water' ELSE 'land' END",
                  _j("report_id"), _j("type"), _j("county"), "'https://nrc.uscg.mil/'"),
    _layer_branch("sdwa_systems", "systems", "water_system", "'water'", _j("pws_id"), _j("name"),
                  _j("county"), _j("dfr_url")),
    _layer_branch("reference_monitors", "monitors", "reference_monitor", "'air'", _j("id"),
                  _j("name"), _j("county")),
    _layer_branch("data_centers", "sites", "data_center", "'land'", _j("id"), _j("name"), _j("county"),
                  "'/site/data_center/' || " + _j("id")),
    _layer_branch("community_sensors", "sensors", "sensor", "'air'", _j("sid"), _j("name")),
    # one site per site_id, placed where its latest reading was taken (DEP sample sites
    # drift a few metres between visits); SQLite takes bare columns from the max(ts) row
    """
    SELECT CASE WHEN source = 'usgs' THEN 'gauge' ELSE 'sample_site' END || ':' || site_id,
           CASE WHEN source = 'usgs' THEN 'gauge' ELSE 'sample_site' END, 'water',
           'water_readings', site_id, site_name, lat, lon, NULL, NULL, NULL, NULL
    FROM (SELECT source, site_id, site_name, lat, lon, max(ts) FROM water_readings
          WHERE lat IS NOT NULL AND lon IS NOT NULL GROUP BY site_id)""",
    # sensors in `readings` (AirNow / AirData reference monitors, PurpleAir), from the
    # small `reading_sites` table filled at ingest; ones a layer above already lists
    # (reference_monitors.json, the resolved community listing) are left to that layer,
    # and an AirData monitor AirNow also reports is left to AirNow (matched on AQS id)
    f"""
    SELECT kind || ':' || sensor_id, kind, 'air', 'reading_sites', sensor_id, name, lat, lon,
           NULL, NULL, NULL,
           json_object('source', source, 'first_ts', first_ts, 'last_ts', last_ts)
    FROM (SELECT CASE WHEN source IN ({_REFERENCE_SOURCES}) THEN 'reference_monitor' ELSE 'sensor' END
                 AS kind, {_AQS_ID} AS aqs_id, * FROM reading_sites
          WHERE lat IS NOT NULL AND lon IS NOT NULL) rs
    WHERE NOT (kind = 'reference_monitor' AND aqs_id IN (
                   SELECT {_j('id')} FROM layer_doc d, json_each(d.doc, '$.monitors') j
                   WHERE d.layer = 'reference_monitors' AND {_j('id')} IS NOT NULL))
      AND NOT (source = 'epa_airdata' AND aqs_id IN (
                   SELECT {_AQS_ID} FROM reading_sites
                   WHERE source = 'airnow' AND lat IS NOT NULL AND lon IS NOT NULL))
      AND NOT (kind = 'sensor' AND sensor_id IN (
                   SELECT {_j('sid')} FROM layer_doc d, json_each(d.doc, '$.sensors') j
                   WHERE d.layer = 'community_sensors' AND {_j('sid')} IS NOT NULL))""",
])

MEASUREMENT_VIEW = "CREATE TEMP VIEW measurement AS" + " UNION ALL".join(
    [f"""
    SELECT 'air' AS medium, source, sensor_id AS ref, ts, '{col}' AS parameter, {col} AS value,
           '{unit}' AS unit, quality, raw
    FROM readings WHERE {col} IS NOT NULL""" for col, unit in AIR_PARAMETERS]
    + ["""
    SELECT 'water', source, site_id, ts, parameter, value, unit, NULL, NULL FROM water_readings"""])


def _rec(layer: str, array: str, kind: str, ref: str, rkind: str, value: str, when: str,
         url: str = "NULL", effective: str = "NULL") -> str:
    return f"""
    SELECT '{kind}:' || {ref} AS site_id, '{layer}' AS layer, '{rkind}' AS kind, {value} AS value,
           {effective} AS effective, {url} AS url, d.fetched_at AS as_of
    FROM layer_doc d, json_each(d.doc, '$.{array}') j WHERE d.layer = '{layer}' AND ({when})"""


RECORD_VIEW = "CREATE TEMP VIEW record AS" + " UNION ALL".join([
    _rec("abandoned_wells", "wells", "well", _j("id"), "orphan", "'orphan'", _j("orphan")),
    _rec("echo_facilities", "facilities", "facility", _j("registry_id"), "violation", _j("status"),
         f"{_j('status')} IN ('significant_violation', 'violation')", _j("echo_url")),
    _rec("echo_facilities", "facilities", "facility", _j("registry_id"), "compliance_status",
         _j("compliance_status"), f"trim(coalesce({_j('compliance_status')}, '')) != ''", _j("echo_url")),
    _rec("coal_npdes", "permits", "permit", _j("permit"), "impaired_303d",
         f"(SELECT group_concat(c.value, ', ') FROM json_each(j.value, '$.impairment_causes') c)",
         _j("impaired"), _j("effluent_url")),
    _rec("dep_mining", "mines", "permit", _j("permit_id"), "permit_stage", _j("permit_status"),
         f"{_j('permit_status')} IS NOT NULL", effective=_j("issue_date")),
    _rec("dep_permits", "permits", "permit", _j("permit_id"), "permit_stage", _j("well_status"),
         f"{_j('well_status')} IS NOT NULL", _j("link"), _j("issue_date")),
    _rec("nrc_spills", "spills", "spill_report", _j("report_id"), "spill", _j("type"), "1",
         "'https://nrc.uscg.mil/'", _j("date")),
    _rec("sdwa_systems", "systems", "water_system", _j("pws_id"), "health_violation",
         _j("contaminants"), _j("health_violation"), _j("dfr_url")),
    _rec("data_centers", "sites", "data_center", _j("id"), "project_status", _j("status"), "1",
         "'/site/data_center/' || " + _j("id")),
])


class ReadModel:
    """Owns the temp views on a Store's SQLite engine and keeps the JSON layers fresh."""

    def __init__(self, engine, data_dir: Path = DATA_DIR):
        self._engine = engine
        self._data_dir = data_dir
        self._mem: dict[str, tuple[int, str]] = {}   # in-memory layers: name -> (version, doc)

    def set_layer(self, name: str, items: list[dict], key: str = "sensors", **meta) -> None:
        """Publish an in-memory layer (e.g. the resolved community-sensor listing)."""
        version = self._mem.get(name, (0, ""))[0] + 1
        self._mem[name] = (version, json.dumps({**meta, key: items}))

    def _setup(self, conn) -> None:
        raw = conn.connection.dbapi_connection
        raw.create_function("hav_mi", 4, hav_mi, deterministic=True)
        raw.create_function("in_county", 2, in_county, deterministic=True)
        conn.exec_driver_sql("CREATE TEMP TABLE IF NOT EXISTS layer_doc ("
                             "layer TEXT PRIMARY KEY, stamp TEXT, fetched_at TEXT, doc TEXT)")
        for ddl in (SITE_VIEW, MEASUREMENT_VIEW, RECORD_VIEW):
            conn.exec_driver_sql(ddl)

    def _refresh(self, conn) -> None:
        have = dict(conn.exec_driver_sql("SELECT layer, stamp FROM layer_doc").all())
        fresh = {}
        for layer, fname in FILE_LAYERS.items():
            path = self._data_dir / fname
            stamp = str(path.stat().st_mtime_ns) if path.is_file() else "missing"
            if have.get(layer) != stamp:
                fresh[layer] = (stamp, path.read_text(encoding="utf-8") if path.is_file() else "{}")
        for layer, (version, doc) in self._mem.items():
            if have.get(layer) != f"v{version}":
                fresh[layer] = (f"v{version}", doc)
        for layer, (stamp, doc) in fresh.items():
            try:
                fetched = json.loads(doc).get("fetched_at")
            except ValueError:
                stamp, doc, fetched = "bad", "{}", None   # a half-written file: retry next call
            conn.exec_driver_sql("INSERT OR REPLACE INTO layer_doc VALUES (?, ?, ?, ?)",
                                 (layer, stamp, fetched, doc))
        if fresh:
            conn.commit()

    @contextmanager
    def connect(self):
        """A connection with ``site`` / ``measurement`` / ``record`` ready and fresh."""
        with self._engine.connect() as conn:
            if not conn.info.get("airwv_readmodel"):
                self._setup(conn)
                conn.info["airwv_readmodel"] = True
            self._refresh(conn)
            yield conn

    # ---- queries ----
    def sites_near(self, conn, lat: float | None, lon: float | None, radius_mi: float, *,
                   layer: str | None = None, kinds: list[str] | None = None,
                   medium: str | None = None, area: str | None = None,
                   county: list[str] | None = None, limit: int = 50,
                   bbox_km: float | None = None) -> list[dict]:
        """Sites within ``radius_mi`` of a point, nearest first (ties in layer order).
        ``area`` keeps sites whose county or WV region (community sensors) is that name;
        with no point (``lat``/``lon`` None) it's the only place filter, in layer order,
        and sites without coordinates count too. ``county`` (canonical names) keeps sites
        whose own county field names one of them; sites with none listed drop out. ``limit``
        -1 is no limit. ``bbox_km`` adds the cheap lat/lon box the water query has always used."""
        params: dict = {"lat": lat, "lon": lon, "r": radius_mi, "limit": limit}
        point = lat is not None and lon is not None
        where = ["lat IS NOT NULL", "lon IS NOT NULL", "hav_mi(:lat, :lon, lat, lon) <= :r"] if point else []
        if area:
            where.append("(county = :area OR json_extract(attrs, '$.region') = :area)")
            params["area"] = area
        if county:
            where.append("in_county(county, :county)")
            params["county"] = ",".join(county)
        if layer:
            where.append("layer = :layer")
            params["layer"] = layer
        if kinds:
            where.append("kind IN (%s)" % ", ".join(f":k{i}" for i in range(len(kinds))))
            params.update({f"k{i}": k for i, k in enumerate(kinds)})
        if medium:
            where.append("medium = :medium")
            params["medium"] = medium
        if bbox_km is not None and point:
            dlat = bbox_km / 111.0
            dlon = bbox_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
            where += ["lat BETWEEN :la0 AND :la1", "lon BETWEEN :lo0 AND :lo1"]
            params.update(la0=lat - dlat, la1=lat + dlat, lo0=lon - dlon, lo1=lon + dlon)
        rows = conn.exec_driver_sql(
            "SELECT site_id, kind, medium, layer, ref, name, lat, lon, county, url, attrs, "
            "hav_mi(:lat, :lon, lat, lon) AS d FROM site WHERE " + (" AND ".join(where) or "1")
            + (" ORDER BY d, ord, ref" if point else " ORDER BY layer, ord, ref")
            + " LIMIT :limit", params).mappings().all()
        return [_site(r) for r in rows]

    def sites(self, conn, *, layer: str, where: str = "1", params: dict | None = None) -> list[dict]:
        rows = conn.exec_driver_sql(
            "SELECT site_id, kind, medium, layer, ref, name, lat, lon, county, url, attrs, NULL AS d "
            f"FROM site WHERE layer = :layer AND ({where}) ORDER BY ord",
            {"layer": layer, **(params or {})}).mappings().all()
        return [_site(r) for r in rows]

    def site(self, conn, site_id: str) -> dict | None:
        """One site by ``site_id`` (the first row where a layer repeats it), or None."""
        r = conn.exec_driver_sql(
            "SELECT site_id, kind, medium, layer, ref, name, lat, lon, county, url, attrs, NULL AS d "
            "FROM site WHERE site_id = :id ORDER BY ord LIMIT 1", {"id": site_id}).mappings().first()
        return _site(r) if r is not None else None

    def series(self, conn, medium: str, ref: str, parameter: str, since=None, until=None,
               limit: int = 5000, over: float | None = None) -> tuple[list[dict], bool]:
        """Measurement rows of ``parameter`` for one ref inside the window (inclusive) and
        above ``over`` when given, oldest first: ``([{ts, value, unit, source, quality}],
        truncated)``. Past ``limit`` rows, the newest ``limit`` are kept and ``truncated``
        is True."""
        window, wp = _window(since, until)
        if over is not None:
            window += " AND value > :over"
            wp["over"] = over
        rows = conn.exec_driver_sql(
            "SELECT ts, value, unit, source, quality FROM measurement WHERE medium = :m "
            f"AND parameter = :p AND ref = :r{window} ORDER BY ts DESC LIMIT :n",
            {"m": medium, "p": parameter, "r": ref, "n": limit + 1, **wp}).mappings().all()
        return [dict(r) for r in reversed(rows[:limit])], len(rows) > limit

    def records_for(self, conn, site_ids: list[str]) -> dict[str, list[dict]]:
        """Records for these sites, grouped by site_id."""
        out: dict[str, list[dict]] = {}
        for i in range(0, len(site_ids), 500):
            chunk = site_ids[i:i + 500]
            marks = ", ".join(f":s{n}" for n in range(len(chunk)))
            for r in conn.exec_driver_sql(
                    f"SELECT site_id, kind, value, effective, url, as_of FROM record WHERE site_id IN ({marks})",
                    {f"s{n}": s for n, s in enumerate(chunk)}).mappings():
                out.setdefault(r["site_id"], []).append(
                    {k: r[k] for k in ("kind", "value", "effective", "url", "as_of")})
        return out

    def alert_records(self, conn, rules: dict) -> list[dict]:
        """The records a record-kind alert can fire on (``alerts.RECORD_RULES``), each with
        its site's place and name, the layer's source and the date it carries, if any."""
        out = []
        for rule, spec in rules.items():
            sites = {s["site_id"]: s for s in self.sites(conn, layer=spec["layer"])}
            source = (self.provenance(conn, [spec["layer"]]).get(spec["layer"]) or {}).get("source")
            for site_id, recs in self.records_for(conn, list(sites)).items():
                s = sites[site_id]
                for r in recs:
                    if r["kind"] not in spec["kinds"]:
                        continue
                    out.append({"rule": rule, "site_id": site_id, "kind": r["kind"],
                                "value": r["value"], "effective": r["effective"],
                                "url": r["url"] or s["url"], "name": s["name"],
                                "lat": s["lat"], "lon": s["lon"], "county": s["county"],
                                # on the county centroid (no exact point in the source)
                                "county_placed": bool(spec.get("county_placed"))
                                and s["attrs"].get("geo") != "exact",
                                "source": source, "attrs": s["attrs"]})
        return out

    def provenance(self, conn, layers: list[str]) -> dict[str, dict]:
        """Each layer file's provenance block (source, scope, disclaimer, fetched_at)."""
        out = {}
        for layer in layers:
            row = conn.exec_driver_sql(
                "SELECT json_extract(doc, '$.source'), json_extract(doc, '$.scope'), "
                "json_extract(doc, '$.disclaimer'), fetched_at FROM layer_doc WHERE layer = ?",
                (layer,)).first()
            if row is not None:
                out[layer] = dict(zip(("source", "scope", "disclaimer", "fetched_at"), row))
        return out

    def latest(self, conn, medium: str, refs: list[str], parameter: str | None = None,
               since=None, until=None) -> dict:
        """Latest value per (ref, parameter): ``{ref: {parameter: {value, unit, ts}}}``,
        optionally inside a time window (``since`` / ``until``, inclusive; a date string
        or a naive-UTC datetime). With one ``parameter``, per-ref indexed LIMIT-1 lookups
        (the fast path on ``readings``, which has millions of rows)."""
        out: dict[str, dict] = {}
        window, wp = _window(since, until)
        if parameter:
            for ref in refs:
                row = conn.exec_driver_sql(
                    "SELECT value, unit, ts FROM measurement WHERE medium = :m AND parameter = :p "
                    f"AND ref = :r{window} ORDER BY ts DESC LIMIT 1",
                    {"m": medium, "p": parameter, "r": ref, **wp}).first()
                if row is not None:
                    out[ref] = {parameter: {"value": row[0], "unit": row[1], "ts": row[2]}}
            return out
        if not refs:
            return out
        marks = ", ".join(f":r{n}" for n in range(len(refs)))
        params = {"medium": medium, **{f"r{n}": r for n, r in enumerate(refs)}, **wp}
        cond = f"medium = :medium AND ref IN ({marks}){window}"
        rows = conn.exec_driver_sql(
            "SELECT m.ref, m.parameter, m.value, m.unit, m.ts FROM measurement m JOIN ("
            f"SELECT ref, parameter, max(ts) AS mts FROM measurement WHERE {cond} GROUP BY ref, parameter"
            ") l ON m.ref = l.ref AND m.parameter = l.parameter AND m.ts = l.mts "
            f"WHERE m.medium = :medium AND m.ref IN ({marks}) ORDER BY m.ts", params).all()
        for ref, param, value, unit, ts in rows:
            out.setdefault(ref, {})[param] = {"value": value, "unit": unit, "ts": ts}
        return out

    def peak(self, conn, medium: str, refs: list[str], parameter: str, since, until=None) -> dict:
        """Highest ``parameter`` value per ref at or after ``since`` (``YYYY-MM-DD``) and
        up to ``until`` when given: ``{ref: {value, unit, ts}}``; per-ref indexed lookups
        like ``latest``."""
        out: dict[str, dict] = {}
        window, wp = _window(since, until)
        for ref in refs:
            row = conn.exec_driver_sql(
                "SELECT value, unit, ts FROM measurement WHERE medium = :m AND parameter = :p "
                f"AND ref = :r{window} ORDER BY value DESC, ts DESC LIMIT 1",
                {"m": medium, "p": parameter, "r": ref, **wp}).first()
            if row is not None:
                out[ref] = {"value": row[0], "unit": row[1], "ts": row[2]}
        return out

    def daily(self, conn, medium: str, ref: str, parameter: str, since=None, until=None,
              max_value: float | None = None) -> dict:
        """Daily mean of ``parameter`` for one ref inside the window: ``{date: value}``
        (aggregated in SQL, like ``Store.daily_avg``). Readings over ``max_value`` are
        left out of the mean."""
        from datetime import date

        window, wp = _window(since, until)
        cap = " AND value <= :cap" if max_value is not None else ""
        rows = conn.exec_driver_sql(
            "SELECT date(ts), avg(value) FROM measurement WHERE medium = :m AND parameter = :p "
            f"AND ref = :r{window}{cap} GROUP BY date(ts)",
            {"m": medium, "p": parameter, "r": ref, **wp, **({"cap": max_value} if cap else {})}).all()
        return {date.fromisoformat(str(d)[:10]): float(v) for d, v in rows if v is not None}

    def parameters(self, conn, medium: str, ref: str) -> list[dict]:
        """What one ref has measured: ``[{parameter, unit, count, last}]``, most rows first."""
        rows = conn.exec_driver_sql(
            "SELECT parameter, unit, count(*) AS count, max(ts) AS last FROM measurement "
            "WHERE medium = :m AND ref = :r GROUP BY parameter ORDER BY count DESC, parameter",
            {"m": medium, "r": ref}).mappings().all()
        return [dict(r) for r in rows]


def _ts(t) -> str:
    """A bound for ``ts``: date strings pass through; datetimes take the stored text form
    (SQLAlchemy's SQLite DateTime), so comparisons match the ORM's."""
    return t if isinstance(t, str) else t.strftime("%Y-%m-%d %H:%M:%S.%f")


def _window(since, until) -> tuple[str, dict]:
    """SQL and params for an inclusive ``ts`` window; a date-only ``until`` covers that day."""
    sql, params = "", {}
    if since is not None:
        sql += " AND ts >= :since"
        params["since"] = _ts(since)
    if until is not None:
        u = _ts(until)
        sql += " AND ts <= :until"
        params["until"] = u + " 99" if len(u) == 10 else u   # sorts after any time that day
    return sql, params


def _site(r) -> dict:
    s = {k: r[k] for k in ("site_id", "kind", "medium", "layer", "ref", "name", "lat", "lon", "county", "url")}
    s["attrs"] = json.loads(r["attrs"]) if r["attrs"] else {}
    s["d"] = r["d"]
    return s
