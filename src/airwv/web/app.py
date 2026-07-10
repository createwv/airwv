"""FastAPI dashboard: read-only API + a single-page UI over stored readings.

Viewing data needs no PurpleAir key — only the database. Run with:

    python -m airwv.web            # then open http://127.0.0.1:8000

Endpoints:
    GET /                          dashboard page
    GET /api/sensors               sensors with coverage
    GET /api/series/{id}?field=    hourly-median time series
    GET /api/diurnal/{id}?field=   time-of-day (local hour) profile
"""

from __future__ import annotations

import os
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from airwv.analysis import detrend_events, hour_of_day_profile
from airwv.export_utils import readings_to_csv
from airwv.storage import Store

# Fields safe to chart (mirror the Reading schema).
FIELDS = ["pm2_5", "pm1_0", "pm10", "voc", "aqi", "temperature", "humidity", "pressure"]


def _parse_date(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


# EPA / PurpleAir PM2.5 AQI categories (µg/m³ upper bounds; last is open-ended).
PM25_BANDS = [
    {"max": 12.0, "label": "Good", "color": "#00e400"},
    {"max": 35.4, "label": "Moderate", "color": "#ffff00"},
    {"max": 55.4, "label": "Unhealthy for sensitive groups", "color": "#ff7e00"},
    {"max": 150.4, "label": "Unhealthy", "color": "#ff0000"},
    {"max": 250.4, "label": "Very unhealthy", "color": "#8f3f97"},
    {"max": None, "label": "Hazardous", "color": "#7e0023"},
]

# EPA PM10 AQI categories (24-hr µg/m³ upper bounds; last is open-ended).
PM10_BANDS = [
    {"max": 54.0, "label": "Good", "color": "#00e400"},
    {"max": 154.0, "label": "Moderate", "color": "#ffff00"},
    {"max": 254.0, "label": "Unhealthy for sensitive groups", "color": "#ff7e00"},
    {"max": 354.0, "label": "Unhealthy", "color": "#ff0000"},
    {"max": 424.0, "label": "Very unhealthy", "color": "#8f3f97"},
    {"max": None, "label": "Hazardous", "color": "#7e0023"},
]

# VOC (Bosch gas index) is relative/uncalibrated — rough guidance only.
VOC_BANDS = [
    {"max": 100.0, "label": "Typical", "color": "#00e400"},
    {"max": 200.0, "label": "Slightly elevated", "color": "#ffff00"},
    {"max": 400.0, "label": "Elevated", "color": "#ff7e00"},
    {"max": None, "label": "High", "color": "#ff0000"},
]


def _pm25_color(value: float | None) -> str:
    """EPA-style color for a PM2.5 concentration (µg/m³)."""
    if value is None:
        return "#9e9e9e"
    for band in PM25_BANDS:
        if band["max"] is None or value <= band["max"]:
            return band["color"]
    return "#7e0023"


def _index_to_name() -> dict[str, str]:
    """Map stored sensor ids (PurpleAir indices) to friendly names, best-effort."""
    try:
        from airwv.registry import load_wv_sensors
        from airwv.resolve import load_index_map

        cache = os.environ.get("AIRWV_INDEX_CACHE", "").strip() or "data/sensor_index_map.json"
        index_map = load_index_map(Path(cache))
        name_by_device = {s.device_id: s.name for s in load_wv_sensors()}
        return {str(idx): name_by_device.get(dev, str(idx)) for dev, idx in index_map.items()}
    except Exception:
        return {}


# WV county → region, for grouping community sensors in the layers tree.
COUNTY_REGION = {
    "Kanawha": "Kanawha Valley", "Putnam": "Kanawha Valley",
    "Wood": "Mid-Ohio Valley", "Mason": "Mid-Ohio Valley", "Pleasants": "Mid-Ohio Valley",
    "Tyler": "Mid-Ohio Valley",
    "Marion": "North Central", "Braxton": "Central WV",
    "Tucker": "Potomac Highlands",
    "Mingo": "Southern Coalfields", "Logan": "Southern Coalfields",
    "Greenbrier": "Greenbrier Valley", "Jefferson": "Eastern Panhandle",
}


def _index_to_region() -> dict[str, str]:
    """Map sensor id (PurpleAir index) to a WV region via the registry county."""
    try:
        from airwv.registry import load_wv_sensors
        from airwv.resolve import load_index_map

        cache = os.environ.get("AIRWV_INDEX_CACHE", "").strip() or "data/sensor_index_map.json"
        index_map = load_index_map(Path(cache))
        region_by_device = {s.device_id: COUNTY_REGION.get(s.county, "Other") for s in load_wv_sensors()}
        return {str(idx): region_by_device.get(dev, "Other") for dev, idx in index_map.items()}
    except Exception:
        return {}


def _index_to_coords() -> dict[str, tuple[float, float]]:
    """Map sensor id -> (lat, lon) from the resolved public listing, best-effort."""
    try:
        import json

        cache = os.environ.get("AIRWV_INDEX_CACHE", "").strip() or "data/sensor_index_map.json"
        listing = Path(cache).with_name("wv_public_sensors.json")
        coords: dict[str, tuple[float, float]] = {}
        for r in json.loads(listing.read_text(encoding="utf-8")):
            lat, lon, idx = r.get("latitude"), r.get("longitude"), r.get("sensor_index")
            if lat is not None and lon is not None and idx is not None:
                coords[str(idx)] = (lat, lon)
        return coords
    except Exception:
        return {}


def create_app(store: Store) -> FastAPI:
    app = FastAPI(title="AirWV Dashboard")
    names = _index_to_name()
    coords = _index_to_coords()
    regions = _index_to_region()

    @app.get("/api/sensors")
    def sensors():
        # SQL aggregation instead of loading every row (was O(all rows) per sensor).
        coverage = store.sensor_coverage()
        latest = store.latest_value_per_sensor("pm2_5")
        ref_ids = set(store.sensor_ids_by_source("openaq"))
        ref_coords = store.coords_from_readings("openaq")
        out = []
        for sid, cov in coverage.items():
            is_ref = sid in ref_ids
            if is_ref:  # reference monitor (OpenAQ/AirNow) — coords on the readings
                lat, lon = ref_coords.get(sid, (None, None))
                name = f"EPA #{sid}"
            else:       # community sensor — coords/name from the resolved listing
                lat, lon = coords.get(sid, (None, None))
                name = names.get(sid, sid)
            latest_pm = latest.get(sid)
            out.append({
                "sensor_id": sid,
                "name": name,
                "kind": "reference" if is_ref else "community",
                "region": None if is_ref else regions.get(sid, "Other"),
                "count": cov["count"],
                "first_ts": cov["first_ts"].isoformat() if cov["first_ts"] else None,
                "last_ts": cov["last_ts"].isoformat() if cov["last_ts"] else None,
                "lat": lat,
                "lon": lon,
                "latest_pm2_5": latest_pm,
                "color": _pm25_color(latest_pm),
            })
        out.sort(key=lambda s: s["name"])
        return out

    @app.get("/api/coverage")
    def coverage():
        c = store.coverage_overall()
        return {"count": c["count"],
                "first_ts": c["first_ts"].isoformat() if c["first_ts"] else None,
                "last_ts": c["last_ts"].isoformat() if c["last_ts"] else None}

    def _check_field(field: str):
        if field not in FIELDS:
            raise HTTPException(status_code=400, detail=f"unknown field {field!r}")

    def _windowed(sensor_id: str, start=None, end=None, default_days: int = 90):
        """Readings bounded to [start, end]; with no start, default to the sensor's
        last ``default_days`` of data — keeps per-sensor endpoints fast at scale
        (the whole history is millions of rows). Query-level (indexed), not Python."""
        lo, hi = _parse_date(start), _parse_date(end)
        since = datetime.combine(lo, time.min) if lo else None
        until = datetime.combine(hi, time.max) if hi else None
        if since is None:
            last = store.last_ts_for_sensor(sensor_id)
            if last is not None:
                since = last - timedelta(days=default_days)
        return store.readings_for_sensor(sensor_id, since=since, until=until)

    @app.get("/api/series/{sensor_id}")
    def series(sensor_id: str, field: str = "pm2_5", start: str | None = None, end: str | None = None):
        _check_field(field)
        buckets: dict = defaultdict(list)
        for r in _windowed(sensor_id, start, end):
            value = getattr(r, field)
            if value is None:
                continue
            buckets[r.ts.replace(minute=0, second=0, microsecond=0)].append(value)
        points = [
            {"ts": ts.isoformat(), "value": round(statistics.median(vals), 1)}
            for ts, vals in sorted(buckets.items())
        ]
        return {"sensor_id": sensor_id, "name": names.get(sensor_id, sensor_id), "field": field, "points": points}

    @app.get("/api/compare")
    def compare(sensors: str, field: str = "pm2_5", start: str | None = None, end: str | None = None):
        _check_field(field)
        from airwv.analysis import diurnal_amplitude

        out = []
        for sid in [s for s in sensors.split(",") if s]:
            amp = diurnal_amplitude(_windowed(sid, start, end), field=field)
            out.append({"sensor_id": sid, "name": names.get(sid, sid), **amp})
        return {"field": field, "sensors": out}

    @app.get("/api/events/{sensor_id}")
    def events(sensor_id: str, field: str = "pm2_5", threshold: float = 6.0,
               start: str | None = None, end: str | None = None):
        _check_field(field)
        found = detrend_events(_windowed(sensor_id, start, end), field=field, z_threshold=threshold)
        return {
            "sensor_id": sensor_id,
            "field": field,
            "events": [
                {"ts": e.ts.isoformat(), "value": e.value, "residual": e.residual, "score": e.score}
                for e in found
            ],
        }

    @app.get("/api/trend/{sensor_id}")
    def trend(sensor_id: str, field: str = "pm2_5", min_days: int = 14,
              start: str | None = None, end: str | None = None):
        _check_field(field)
        from airwv.analysis import is_worsening, linear_trend

        t = linear_trend(_windowed(sensor_id, start, end), field=field, min_days=min_days)
        return {
            "sensor_id": sensor_id, "field": field, "direction": t.direction,
            "pct_change": t.pct_change, "slope_per_30d": t.slope_per_30d,
            "first": t.first, "last": t.last, "r": t.r, "watch": is_worsening(t),
        }

    @app.get("/api/diurnal/{sensor_id}")
    def diurnal(sensor_id: str, field: str = "voc", start: str | None = None, end: str | None = None):
        _check_field(field)
        profile = hour_of_day_profile(_windowed(sensor_id, start, end), field=field)
        return {
            "sensor_id": sensor_id,
            "name": names.get(sensor_id, sensor_id),
            "field": field,
            "hours": [{"hour": s.hour, "median": s.median, "count": s.count} for s in profile],
        }

    @app.get("/api/sources")
    def sources():
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "sources.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"tier": data.get("tier"), "disclaimer": data.get("disclaimer"),
                    "sources": data.get("sources", [])}
        except Exception:
            return {"tier": "documented", "disclaimer": "", "sources": []}

    @app.get("/api/reference-monitors")
    def reference_monitors():
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "reference_monitors.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"note": data.get("note"), "monitors": data.get("monitors", [])}
        except Exception:
            return {"note": "", "monitors": []}

    @app.get("/api/validate")
    def validate(correct: bool = False):
        """Community sensors vs their nearest regulatory reference monitor."""
        from airwv.ingest import run_validate

        results = run_validate(store=store, coords=coords, correct=correct)
        for r in results:
            r["sensor_name"] = names.get(r["sensor"], r["sensor"])
        note = ("Each community sensor is paired with its nearest regulatory "
                "reference monitor (OpenAQ/AirNow). r = daily-median correlation "
                "(1.0 = perfect tracking); bias = sensor − reference (µg/m³). "
                "High r validates the sensor; a wild bias flags a malfunction.")
        if correct:
            note += (" PM2.5 is EPA-corrected (Barkjohn 2021), which pulls raw "
                     "PurpleAir's high bias down toward reference grade.")
        return {"results": results, "corrected": correct, "note": note}

    @app.get("/api/guide")
    def guide():
        return {
            "pm2_5": PM25_BANDS,
            "pm10": PM10_BANDS,
            "voc": VOC_BANDS,
            "voc_note": "VOC is a relative gas index (uncalibrated, drifts per sensor) — trends and comparisons at one sensor are meaningful; absolute cross-sensor values are not.",
        }

    @app.get("/api/export/{sensor_id}.csv")
    def export_csv(sensor_id: str):
        csv_text = readings_to_csv(store.readings_for_sensor(sensor_id))
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="airwv_{sensor_id}.csv"'},
        )

    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


def _default_store() -> Store:
    url = os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite"
    return Store(url)


app = create_app(_default_store())


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Empower WV — Community Eco Monitoring</title>
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="icon" type="image/png" href="/static/favicon.png">
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  /* Empower WV brand palette — blue / gold / purple. */
  :root { --brand:#4a7fb0; --brand-accent:#c9992f; --brand-purple:#7a6fb0; --sky:#a9c4dc; --bg:#f4f7f9; }
  body { font-family: system-ui, sans-serif; margin: 0; background:var(--bg); color:#1a1a1a; }
  /* Parallax hero: still sky gradient + panning cloud layers + still logo.
     Height scales with viewport width -> more sky on desktop, compact on mobile. */
  header { position:relative; overflow:hidden; height:clamp(185px, 24vw, 320px);
    background:linear-gradient(to top, #f7ca89 4%, #e4d1ab 18%, #cddcd7 38%, #c5e0e8 49%, #a1bcd1 82%);
    display:flex; align-items:center; justify-content:center; }
  header .cloud { position:absolute; bottom:0; left:50%; width:120%; height:auto;
    transform:translateX(-50%); pointer-events:none; }
  /* Same duration so all layers turn the corners together. Gold(back) + white
     (front) sway one way; blue(mid) counter-sways (alternate-reverse). Amplitude
     gives the depth (subtle back, bold front) and bumps up on desktop. */
  header .c-gold  { --amp:16px; animation: sway 30s ease-in-out infinite alternate; }
  header .c-blue  { --amp:24px; animation: sway 30s ease-in-out infinite alternate-reverse; }
  header .c-white { --amp:36px; animation: sway 30s ease-in-out infinite alternate; }
  @media (min-width:820px){
    header .c-gold  { --amp:30px; }
    header .c-blue  { --amp:46px; }
    header .c-white { --amp:66px; }
  }
  @keyframes sway { 0%,9%{transform:translateX(calc(-50% - var(--amp)))} 91%,100%{transform:translateX(calc(-50% + var(--amp)))} }
  @media (prefers-reduced-motion:reduce){ header .cloud{animation:none} }
  /* Logo: floors ~165px on narrow screens (≈30vw near 570px), scales at 20vw
     through desktop, capped so it stays in the banner on big monitors. */
  header img.logo { position:relative; z-index:2; height:clamp(165px, 20vw, 230px); width:auto; max-width:92%;
    filter:drop-shadow(0 1px 2px rgba(0,0,0,.12)); }
  .subbar { background:linear-gradient(90deg,var(--brand),var(--brand-purple)); color:#fff;
    padding:8px 20px; font-size:13px; text-align:center; }
  .betabar { background:#fff4d6; color:#7a5b12; border-bottom:1px solid #f0e0b0;
    padding:6px 20px; font-size:12.5px; text-align:center; }
  .betabar b { color:#8a4b00; }
  .controls { padding:14px 20px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  select { padding:6px 10px; font-size:14px; }
  .meta { color:#666; font-size:13px; }
  .card { background:#fff; margin:16px 20px; border:1px solid #e5e5e8; border-radius:8px; position:relative; }
  .busyover { position:absolute; inset:0; display:none; align-items:center; justify-content:center;
    background:rgba(255,255,255,.6); z-index:600; border-radius:8px; }
  .busyover.on { display:flex; }
  .spinner { width:30px; height:30px; border:3px solid #d3dbe4; border-top-color:var(--brand);
    border-radius:50%; animation:spin .8s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .card h2 { font-size:14px; margin:0; padding:10px 14px; border-bottom:1px solid #eee; color:#444; }
  .chart { height:320px; }
  #map { height:360px; border-radius:0 0 8px 8px; }
  .legend { font-size:12px; color:#666; padding:6px 14px; }
  .sensorlist { display:flex; flex-direction:column; gap:2px; max-height:120px; overflow:auto;
    border:1px solid #ddd; border-radius:6px; padding:6px 10px; font-size:13px; min-width:200px; }
  .layerbar { padding:2px 20px 4px; }
  .layers { font-size:13px; display:flex; gap:22px; flex-wrap:wrap; align-items:flex-start; }
  .layers > b { align-self:center; }
  .layers details { min-width:150px; }
  .layers summary { cursor:pointer; user-select:none; padding:1px 0; }
  .layers .children { padding:2px 0 0 20px; display:flex; flex-direction:column; gap:1px; }
  .layers label { display:block; }
  .layers .cnt { color:#aaa; font-size:11px; }
  .layers .srow { cursor:pointer; padding:1px 2px; border-radius:3px; }
  .layers .srow:hover { color:var(--brand); background:#f0f4f8; }
  .layers .srow.on { font-weight:700; color:#111; }
  .layers .srow.on::before { content:'●'; color:var(--brand-accent); margin-right:4px; font-size:10px; }
  button { padding:6px 12px; font-size:13px; cursor:pointer; }
  table { width:calc(100% - 28px); margin:10px 14px 14px; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px solid #eee; }
  th { color:#666; font-weight:600; }
  .guide { padding:10px 14px; display:flex; flex-wrap:wrap; gap:8px; }
  .guide .chip { display:flex; align-items:center; gap:6px; font-size:12px; border:1px solid #eee;
    border-radius:6px; padding:4px 8px; }
  .guide .sw { width:14px; height:14px; border-radius:3px; border:1px solid #0002; }
  .guide .note { flex-basis:100%; color:#666; font-size:12px; margin-top:4px; }
  .about { padding:10px 14px; font-size:13px; color:#444; max-width:70ch; }
  .about a { color:var(--brand); }
  footer { text-align:center; color:#888; font-size:12px; padding:20px; }
  footer a { color:var(--brand-accent); }
</style>
</head>
<body>
<header>
  <img class="cloud c-gold"  src="/static/banner-clouds-gold.svg"  alt="">
  <img class="cloud c-blue"  src="/static/banner-clouds-blue.svg"  alt="">
  <img class="cloud c-white" src="/static/banner-clouds-white.svg" alt="">
  <img class="logo" src="/static/logo.svg" alt="empower wv — community eco monitoring">
</header>
<div class="subbar">West Virginia community air quality · map colored by latest PM2.5 · times in US Eastern</div>
<div class="betabar">🚧 <b>Beta — under construction.</b> Readings are provisional and shown for community awareness, not regulatory use.</div>
<div class="controls">
  <label>Metric <select id="field">
    <option value="pm2_5">PM2.5</option>
    <option value="voc">VOC</option>
    <option value="pm10">PM10</option>
    <option value="pm1_0">PM1.0</option>
    <option value="temperature">Temperature</option>
    <option value="humidity">Humidity</option>
  </select></label>
  <label>From <input type="date" id="start"></label>
  <label>To <input type="date" id="end"></label>
  <button id="clear">Clear dates</button>
  <a id="dl" href="#" download>⬇ Download CSV</a>
</div>
<div class="layerbar"><div id="layers" class="layers"></div></div>
<div class="meta" id="coverage" style="padding:0 20px 8px"></div>
<div class="card"><h2>Sensor map</h2><div class="busyover on" id="b-map"><div class="spinner"></div></div><div id="map"></div>
  <div class="legend">PM2.5 µg/m³: <span style="color:#00e400">●</span> &lt;12
    <span style="color:#e0d000">●</span> &lt;35 <span style="color:#ff7e00">●</span> &lt;55
    <span style="color:#ff0000">●</span> &lt;150 <span style="color:#8f3f97">●</span> higher · ◎ ringed = reference monitor · click a community marker to chart it</div>
</div>
<div class="card"><h2>Time series (hourly median) — red × = events, dashed = trend (single sensor)
  <span class="meta" id="trendinfo"></span></h2><div class="busyover" id="b-ts"><div class="spinner"></div></div><div id="ts" class="chart"></div>
  <div class="meta" id="detail" style="padding:6px 14px">Click a point (or event ×) for detail.</div>
</div>
<div class="card"><h2>Time-of-day profile (median by local hour, ET)</h2><div class="busyover" id="b-di"><div class="spinner"></div></div><div id="diurnal" class="chart"></div></div>
<div class="card"><h2>Day vs. overnight compare</h2>
  <table id="cmp"><thead><tr><th>Sensor</th><th>Day (9-17)</th><th>Night (0-5)</th><th>Night/Day</th></tr></thead><tbody></tbody></table>
</div>
<div class="card"><h2>Validation — community sensors vs. regulatory reference monitors
  <label style="float:right;font-weight:400;font-size:12px"><input type="checkbox" id="epacorrect"> EPA-corrected PM2.5</label></h2>
  <table id="validate"><thead><tr><th>Community sensor</th><th>Nearest reference monitor</th>
    <th>Distance</th><th>Days</th><th>Correlation (r)</th><th>Bias vs. reference</th></tr></thead><tbody></tbody></table>
  <div class="meta" id="validatenote" style="padding:0 14px 12px"></div>
</div>
<div class="card"><h2>Health guide — what the levels mean <span class="meta" style="font-weight:400">(for the selected metric)</span></h2><div id="guide" class="guide"></div></div>
<div class="card"><h2>About AirWV</h2>
  <div class="about">
    <p><b>Empower WV — community eco monitoring</b> is a community air-quality
    initiative for West Virginia, powered by the open-source AirWV project. It
    collects readings from community <a href="https://www2.purpleair.com/"
    target="_blank" rel="noopener">PurpleAir</a> sensors, stores long-term history,
    and surfaces trends, anomalies, and alerts. It grew out of the
    <a href="https://createwv.org/projects/air-monitoring/" target="_blank" rel="noopener">Kanawha
    Valley Air Quality Monitoring project</a> (Create WV, WVCAG, and partners).</p>
    <p>Data shown is from community sensors and is provided as-is for awareness, not
    regulatory use. Source &amp; docs:
    <a href="https://github.com/createwv/airwv" target="_blank" rel="noopener">github.com/createwv/airwv</a>.</p>
  </div>
</div>
<footer>AirWV · open-source · <a href="https://github.com/createwv/airwv" target="_blank" rel="noopener">GitHub</a></footer>
<script>
const $ = id => document.getElementById(id);
const j = async u => (await fetch(u)).json();
const busy = (id,on) => { const el=$(id); if(el) el.classList.toggle('on', on); };
const COLORS = ['#3b2a6b','#e07b00','#1b9e77','#d62728','#7570b3','#17becf','#b8860b'];
let map, markers = {}, allSensors = [], GUIDE = null;
let chartSet = new Set();   // sensor ids currently plotted (click a row or a map dot to toggle)
function toggleChart(sid){
  if (chartSet.has(sid)) chartSet.delete(sid); else chartSet.add(sid);
  // update the tree row(s) in place (don't rebuild — that would collapse open groups)
  document.querySelectorAll(`.srow[data-sid="${sid}"]`).forEach(el => el.classList.toggle('on', chartSet.has(sid)));
  redrawSensors(); render();
}

const GUIDE_META = {pm2_5:['PM2.5',' µg/m³'], pm10:['PM10',' µg/m³'], voc:['VOC',' (relative index)']};
function guideBands(field){ return field==='pm2_5'?GUIDE?.pm2_5 : field==='pm10'?GUIDE?.pm10 : field==='voc'?GUIDE?.voc : null; }
async function loadGuide(){ GUIDE = await j('/api/guide'); renderGuide(); }
function renderGuide(){
  if (!GUIDE) return;
  const field = $('field').value, arr = guideBands(field);
  if (!arr){
    $('guide').innerHTML = `<div class="note">No standard health thresholds for this metric — watch trends and cross-sensor comparisons instead.</div>`;
    return;
  }
  const [label, unit] = GUIDE_META[field];
  const chips = arr.map((b,i) => {
    const lo = i === 0 ? 0 : arr[i-1].max;
    const range = b.max == null ? `${lo}+` : `${lo}–${b.max}`;
    return `<span class="chip"><span class="sw" style="background:${b.color}"></span>${b.label} (${range}${unit})</span>`;
  }).join('');
  $('guide').innerHTML = `<b style="flex-basis:100%">${label}${unit}</b>` + chips +
    (field==='voc' ? `<div class="note">${GUIDE.voc_note}</div>` : '');
}
function bandShapes(field, yMax){
  const arr = guideBands(field);
  if (!arr) return [];
  const shapes = [];
  arr.forEach((b,i) => {
    const lo = i === 0 ? 0 : arr[i-1].max;
    const hi = b.max == null ? yMax : Math.min(b.max, yMax);
    if (hi <= lo) return;
    shapes.push({type:'rect', xref:'paper', x0:0, x1:1, yref:'y', y0:lo, y1:hi,
      fillcolor:b.color, opacity:0.10, line:{width:0}, layer:'below'});
  });
  return shapes;
}

async function loadSensors(){
  allSensors = await j('/api/sensors');
  // default: chart EWV Glasgow 1 (falls back to the first community sensor)
  const glasgow = allSensors.find(s => /glasgow/i.test(s.name)) ||
                  allSensors.find(s => s.kind === 'community' && s.lat != null);
  if (glasgow) chartSet.add(glasgow.sensor_id);
  drawMap(allSensors);
  render();
}
async function loadCoverage(){
  const c = await j('/api/coverage');
  if (c.first_ts) $('coverage').textContent =
    `Showing all data · ${Number(c.count).toLocaleString()} readings · first ${c.first_ts.slice(0,10)} → last ${c.last_ts.slice(0,10)}`;
}
const layerState = {community:true, reference:true, sources:true, regions:{}, cats:{}};
function drawMap(sensors){
  if (!map){
    map = L.map('map').setView([38.35, -81.6], 8);
    const tile = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom:18, attribution:'© OpenStreetMap'}).addTo(map);
    tile.on('load', () => busy('b-map', false));      // hide spinner once tiles are in
    setTimeout(() => busy('b-map', false), 5000);     // fallback
  }
  redrawSensors();
  const pts = allSensors.filter(s => s.kind !== 'reference' && s.lat != null).map(s => [s.lat, s.lon]);
  if (pts.length) map.fitBounds(pts, {padding:[30,30], maxZoom:11});
  loadSources();
}
function redrawSensors(){
  if (sensorLayer) sensorLayer.remove();
  if (refLayer) refLayer.remove();
  sensorLayer = L.layerGroup();
  refLayer = L.layerGroup();
  allSensors.forEach(x => {
    if (x.lat == null || x.lon == null) return;
    const charted = chartSet.has(x.sensor_id);
    // charted markers get a gold highlight ring so map ⇔ chart stay in sync
    const ring = charted ? {color:'#c9992f', weight:4} : null;
    const pop = `<b>${x.name}</b>${x.kind==='reference'?'<br><i>reference monitor (EPA/AirNow)</i>':''}`+
      `<br>latest PM2.5: ${x.latest_pm2_5 ?? '—'}<br>${Number(x.count).toLocaleString()} readings`+
      `<br><small>${charted?'✓ charted — click to remove':'click to add to chart'}</small>`;
    if (x.kind === 'reference') {
      if (!layerState.reference) return;
      L.circleMarker([x.lat, x.lon], {radius:8, color:(ring?ring.color:'#111'), weight:(ring?ring.weight:3), fillColor:x.color, fillOpacity:0.85})
        .bindPopup(pop).on('click', () => toggleChart(x.sensor_id)).addTo(refLayer);
    } else {
      if (!layerState.community || layerState.regions[x.region] === false) return;
      L.circleMarker([x.lat, x.lon], {radius:9, color:(ring?ring.color:'#333'), weight:(ring?ring.weight:1), fillColor:x.color, fillOpacity:0.9})
        .bindPopup(pop).on('click', () => toggleChart(x.sensor_id)).addTo(sensorLayer);
    }
  });
  sensorLayer.addTo(map);
  refLayer.addTo(map);
}
let sensorLayer, refLayer;
// reference monitors are now drawn live in drawMap() (ringed circles, current PM2.5)
const SRC_ICON = {power:'⚡', chemical:'⚗️', oil_gas:'🛢️', materials:'⛏️', waste:'🗑️', other:'🏭'};
const SRC_LABEL = {power:'Power plant', chemical:'Chemical', oil_gas:'Oil & gas',
  materials:'Metals / mining / materials', waste:'Waste', other:'Other TRI facility'};
let sourceLayer, allSources_ = [], srcDisclaimer = '';
async function loadSources(){
  const data = await j('/api/sources');
  allSources_ = data.sources; srcDisclaimer = data.disclaimer || '';
  redrawSources();
  buildLayers();
}
function redrawSources(){
  if (sourceLayer) sourceLayer.remove();
  sourceLayer = L.layerGroup();
  if (layerState.sources) allSources_.forEach(s => {
    if (s.lat == null || s.lon == null) return;
    const cat = s.category || 'other';
    if (layerState.cats[cat] === false) return;
    L.marker([s.lat, s.lon], {icon: L.divIcon({className:'', html:SRC_ICON[cat]||'🏭',
      iconSize:[22,22], iconAnchor:[11,11]})})
      .bindPopup(`<b>${s.name}</b>${s.state?` <small>(${s.state})</small>`:''}`+
        `<br><b style="color:#7a5b12">${SRC_LABEL[cat]||'Facility'}</b> · ${s.type}<br><i>${s.operator||''}</i>`+
        `<br><small>Documented public-record facility · ${s.citation||''}</small>`+
        `<br><small style="color:#a00">${srcDisclaimer}</small>`)
      .addTo(sourceLayer);
  });
  sourceLayer.addTo(map);
}
// ---- Collapsible map-layers tree (community by region · reference · sources by category) ----
function groupCount(arr, key){ const m={}; arr.forEach(x=>{ const k=x[key]; if(k) m[k]=(m[k]||0)+1; }); return m; }
function buildLayers(){
  const comm = allSensors.filter(s => s.kind==='community' && s.lat!=null);
  const ref = allSensors.filter(s => s.kind==='reference' && s.lat!=null);
  const rCounts = groupCount(comm,'region'), cCounts = groupCount(allSources_,'category');
  const regions = Object.keys(rCounts).sort();
  const cats = ['power','chemical','oil_gas','materials','waste','other'].filter(c=>cCounts[c]);
  regions.forEach(r=>{ if(!(r in layerState.regions)) layerState.regions[r]=true; });
  cats.forEach(c=>{ if(!(c in layerState.cats)) layerState.cats[c]=true; });
  const row = (attr,val,checked,label,cnt)=>
    `<label><input type="checkbox" data-${attr}="${val}" ${checked?'checked':''}> ${label} <span class="cnt">${cnt}</span></label>`;
  // community: each region is a <details> (visibility checkbox) holding clickable sensor rows
  const byRegion = {};
  comm.forEach(s => { (byRegion[s.region] = byRegion[s.region] || []).push(s); });
  const regionBlocks = regions.map(r => {
    const rows = (byRegion[r]||[]).sort((a,b)=>a.name.localeCompare(b.name)).map(s =>
      `<div class="srow${chartSet.has(s.sensor_id)?' on':''}" data-sid="${s.sensor_id}">${s.name}</div>`).join('');
    return `<details><summary><input type="checkbox" data-region="${r}" ${layerState.regions[r]?'checked':''}> ${r} <span class="cnt">${rCounts[r]}</span></summary><div class="children">${rows}</div></details>`;
  }).join('');
  const catRows = cats.map(c=>row('cat',c,layerState.cats[c],`${SRC_ICON[c]} ${SRC_LABEL[c]}`,cCounts[c])).join('');
  $('layers').innerHTML =
    `<b style="font-size:12px;color:#555">Sensors &amp; layers <span class="cnt">(click a sensor to chart it)</span></b>`+
    `<details open><summary><input type="checkbox" id="L-community" ${layerState.community?'checked':''}> ● Community sensors <span class="cnt">${comm.length}</span></summary><div class="children">${regionBlocks}</div></details>`+
    `<label style="align-self:center"><input type="checkbox" id="L-reference" ${layerState.reference?'checked':''}> ◎ Reference monitors <span class="cnt">${ref.length}</span></label>`+
    `<details><summary><input type="checkbox" id="L-sources" ${layerState.sources?'checked':''}> 🏭 Pollution sources <span class="cnt">${allSources_.length}</span></summary><div class="children">${catRows}</div></details>`;
  // checkboxes in a <summary> shouldn't toggle its open/close
  $('layers').querySelectorAll('summary input[type=checkbox]').forEach(cb=> cb.addEventListener('click', e=>e.stopPropagation()));
  // updates happen in place (no rebuild) so open groups stay open
  $('L-community').onchange = e=>{ layerState.community=e.target.checked;
    regions.forEach(r=>layerState.regions[r]=e.target.checked);
    $('layers').querySelectorAll('[data-region]').forEach(cb=>{cb.checked=e.target.checked;cb.indeterminate=false;}); redrawSensors(); };
  $('L-reference').onchange = e=>{ layerState.reference=e.target.checked; redrawSensors(); };
  $('L-sources').onchange = e=>{ layerState.sources=e.target.checked;
    cats.forEach(c=>layerState.cats[c]=e.target.checked);
    $('layers').querySelectorAll('[data-cat]').forEach(cb=>cb.checked=e.target.checked); redrawSources(); };
  $('layers').querySelectorAll('[data-region]').forEach(cb=> cb.onchange=e=>{
    layerState.regions[e.target.dataset.region]=e.target.checked;
    layerState.community=regions.some(r=>layerState.regions[r]); redrawSensors(); syncParents(regions,cats); });
  $('layers').querySelectorAll('[data-cat]').forEach(cb=> cb.onchange=e=>{
    layerState.cats[e.target.dataset.cat]=e.target.checked;
    layerState.sources=cats.some(c=>layerState.cats[c]); redrawSources(); syncParents(regions,cats); });
  $('layers').querySelectorAll('.srow').forEach(rowEl=> rowEl.addEventListener('click', ()=> toggleChart(rowEl.dataset.sid)));
  syncParents(regions,cats);
}
function syncParents(regions,cats){
  const cOn=regions.filter(r=>layerState.regions[r]).length, sOn=cats.filter(c=>layerState.cats[c]).length;
  const cbC=$('L-community'), cbS=$('L-sources');
  if(cbC){ cbC.checked=cOn>0; cbC.indeterminate=cOn>0 && cOn<regions.length; }
  if(cbS){ cbS.checked=sOn>0; cbS.indeterminate=sOn>0 && sOn<cats.length; }
}

async function loadValidation(){
  const d = await j('/api/validate?correct=' + ($('epacorrect').checked ? 'true' : 'false'));
  const tb = document.querySelector('#validate tbody');
  if (!d.results.length){
    tb.innerHTML = '<tr><td colspan="6" style="color:#888">No reference data yet — '+
      'run <code>ingest reference</code> then it appears here.</td></tr>';
    $('validatenote').textContent = ''; return;
  }
  tb.innerHTML = d.results.map(v => {
    const r = v.r;
    const rcol = r==null ? '#888' : r>=0.7 ? '#1b9e77' : r>=0.4 ? '#e07b00' : '#d62728';
    const rtxt = r==null ? 'n/a' : r.toFixed(2);
    const bad = Math.abs(v.bias) > 50;   // an absurd bias flags a malfunctioning sensor
    const btxt = (v.bias>0?'+':'') + v.bias.toFixed(1) + ' µg/m³';
    return `<tr>
      <td>${v.sensor_name}</td>
      <td>monitor #${v.monitor}</td>
      <td>${v.distance_km} km</td>
      <td>${v.days}</td>
      <td style="color:${rcol};font-weight:600">${rtxt}</td>
      <td style="color:${bad?'#d62728':'#444'};font-weight:${bad?'600':'400'}">${btxt}${bad?' ⚠ malfunction?':''}</td>
    </tr>`;
  }).join('');
  $('validatenote').textContent = d.note;
}

const selected = () => [...chartSet];
function range(){ const s=$('start').value, e=$('end').value;
  return (s?`&start=${s}`:'') + (e?`&end=${e}`:''); }

const nameOf = id => (allSensors.find(s=>s.sensor_id===id)||{}).name || id;
async function render(){
  const ids = selected(), field = $('field').value, rng = range();
  $('dl').href = ids.length ? `/api/export/${ids[0]}.csv` : '#';
  if (!ids.length){
    Plotly.newPlot('ts', [], {margin:{t:10,r:10,b:40,l:45},
      annotations:[{text:'Click a sensor (map or list) to chart it', showarrow:false, font:{color:'#999'}}]},
      {responsive:true, displayModeBar:false});
    Plotly.newPlot('diurnal', [], {margin:{t:10,r:10,b:40,l:45}}, {responsive:true, displayModeBar:false});
    $('cmp').querySelector('tbody').innerHTML = '';
    $('trendinfo').textContent = '';
    return;
  }
  busy('b-ts', true); busy('b-di', true);
  const tsTraces = [], diTraces = [];
  const series = await Promise.all(ids.map(id => j(`/api/series/${id}?field=${field}${rng}`)));
  series.forEach((s,i) => tsTraces.push({x:s.points.map(p=>p.ts), y:s.points.map(p=>p.value),
    mode:'lines', name:s.name, line:{color:COLORS[i%COLORS.length], width:1.3}}));
  if (ids.length === 1){
    const [ev, tr] = await Promise.all([
      j(`/api/events/${ids[0]}?field=${field}${rng}`),
      j(`/api/trend/${ids[0]}?field=${field}${rng}`),
    ]);
    if (ev.events.length) tsTraces.push({x:ev.events.map(e=>e.ts), y:ev.events.map(e=>e.value),
      mode:'markers', name:'event', marker:{color:'#e00', symbol:'x', size:9},
      customdata: ev.events.map(e=>[e.residual, e.score])});
    const pts = series[0].points;
    if (tr.first != null && pts.length){
      tsTraces.push({x:[pts[0].ts, pts[pts.length-1].ts], y:[tr.first, tr.last],
        mode:'lines', name:'trend', line:{dash:'dash', color:'#111', width:2}});
    }
    $('trendinfo').textContent = tr.direction === 'insufficient' ? '' :
      `trend: ${tr.direction}${tr.watch ? ' ⚠ watch' : ''} · Δ${tr.pct_change}% over period (r=${tr.r})`;
  } else { $('trendinfo').textContent = ''; }
  const yMax = Math.max(1, ...tsTraces.flatMap(t => t.y.filter(v => v != null)));
  const gd = await Plotly.newPlot('ts', tsTraces, {margin:{t:10,r:10,b:40,l:45},
    yaxis:{title:field}, legend:{orientation:'h'}, shapes:bandShapes(field, yMax)},
    {responsive:true, displayModeBar:false});
  gd.on('plotly_click', e => {
    const p = e.points[0];
    let msg = `${p.x} — ${field} = ${p.y}`;
    if (p.data.name === 'event' && p.customdata)
      msg += `  ·  event: +${p.customdata[0]} over baseline (z=${p.customdata[1]})`;
    $('detail').textContent = msg;
  });
  gd.on('plotly_legendclick', e => {           // click a legend name to remove that sensor
    const nm = e.data[e.curveNumber].name;
    if (nm === 'event' || nm === 'trend') return true;
    const sid = [...chartSet].find(id => nameOf(id) === nm);
    if (sid) { toggleChart(sid); return false; }
    return true;
  });

  busy('b-ts', false);
  const dis = await Promise.all(ids.map(id => j(`/api/diurnal/${id}?field=${field}${rng}`)));
  dis.forEach((d,i) => diTraces.push({x:d.hours.map(h=>h.hour), y:d.hours.map(h=>h.median),
    mode:'lines+markers', name:d.name, line:{color:COLORS[i%COLORS.length]}}));
  Plotly.newPlot('diurnal', diTraces, {margin:{t:10,r:10,b:40,l:45},
    xaxis:{title:'hour of day (ET)', dtick:2}, yaxis:{title:field}, legend:{orientation:'h'}},
    {responsive:true, displayModeBar:false});
  busy('b-di', false);

  const cmp = await j(`/api/compare?sensors=${ids.join(',')}&field=${field}${rng}`);
  $('cmp').querySelector('tbody').innerHTML = cmp.sensors.map(s =>
    `<tr><td>${s.name}</td><td>${s.day ?? '—'}</td><td>${s.night ?? '—'}</td>
     <td><b>${s.night_day_ratio ?? '—'}</b></td></tr>`).join('');
}
$('field').addEventListener('change', () => { render(); renderGuide(); });
$('start').addEventListener('change', render);
$('end').addEventListener('change', render);
$('clear').addEventListener('click', () => { $('start').value=''; $('end').value=''; render(); });
// map layer visibility is driven by the layers tree (buildLayers)
loadGuide().then(loadSensors);
loadValidation();
loadCoverage();
$('epacorrect').addEventListener('change', loadValidation);
</script>
</body>
</html>"""
