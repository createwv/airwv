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
from datetime import date, datetime
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

    @app.get("/api/sensors")
    def sensors():
        out = []
        for sid in store.distinct_sensor_ids():
            rows = store.readings_for_sensor(sid)
            if not rows:
                continue
            latest_pm = next((r.pm2_5 for r in reversed(rows) if r.pm2_5 is not None), None)
            lat, lon = coords.get(sid, (None, None))
            out.append({
                "sensor_id": sid,
                "name": names.get(sid, sid),
                "count": len(rows),
                "first_ts": rows[0].ts.isoformat(),
                "last_ts": rows[-1].ts.isoformat(),
                "lat": lat,
                "lon": lon,
                "latest_pm2_5": latest_pm,
                "color": _pm25_color(latest_pm),
            })
        out.sort(key=lambda s: s["name"])
        return out

    def _check_field(field: str):
        if field not in FIELDS:
            raise HTTPException(status_code=400, detail=f"unknown field {field!r}")

    @app.get("/api/series/{sensor_id}")
    def series(sensor_id: str, field: str = "pm2_5", start: str | None = None, end: str | None = None):
        _check_field(field)
        lo = _parse_date(start)
        hi = _parse_date(end)
        buckets: dict = defaultdict(list)
        for r in store.readings_for_sensor(sensor_id):
            value = getattr(r, field)
            if value is None:
                continue
            day = r.ts.date()
            if (lo and day < lo) or (hi and day > hi):
                continue
            buckets[r.ts.replace(minute=0, second=0, microsecond=0)].append(value)
        points = [
            {"ts": ts.isoformat(), "value": round(statistics.median(vals), 1)}
            for ts, vals in sorted(buckets.items())
        ]
        return {"sensor_id": sensor_id, "name": names.get(sensor_id, sensor_id), "field": field, "points": points}

    @app.get("/api/compare")
    def compare(sensors: str, field: str = "pm2_5"):
        _check_field(field)
        from airwv.analysis import diurnal_amplitude

        out = []
        for sid in [s for s in sensors.split(",") if s]:
            amp = diurnal_amplitude(store.readings_for_sensor(sid), field=field)
            out.append({"sensor_id": sid, "name": names.get(sid, sid), **amp})
        return {"field": field, "sensors": out}

    @app.get("/api/events/{sensor_id}")
    def events(sensor_id: str, field: str = "pm2_5", threshold: float = 6.0):
        _check_field(field)
        found = detrend_events(store.readings_for_sensor(sensor_id), field=field, z_threshold=threshold)
        return {
            "sensor_id": sensor_id,
            "field": field,
            "events": [
                {"ts": e.ts.isoformat(), "value": e.value, "residual": e.residual, "score": e.score}
                for e in found
            ],
        }

    @app.get("/api/trend/{sensor_id}")
    def trend(sensor_id: str, field: str = "pm2_5", min_days: int = 14):
        _check_field(field)
        from airwv.analysis import is_worsening, linear_trend

        t = linear_trend(store.readings_for_sensor(sensor_id), field=field, min_days=min_days)
        return {
            "sensor_id": sensor_id, "field": field, "direction": t.direction,
            "pct_change": t.pct_change, "slope_per_30d": t.slope_per_30d,
            "first": t.first, "last": t.last, "r": t.r, "watch": is_worsening(t),
        }

    @app.get("/api/diurnal/{sensor_id}")
    def diurnal(sensor_id: str, field: str = "voc", start: str | None = None, end: str | None = None):
        _check_field(field)
        lo, hi = _parse_date(start), _parse_date(end)
        rows = [
            r for r in store.readings_for_sensor(sensor_id)
            if not ((lo and r.ts.date() < lo) or (hi and r.ts.date() > hi))
        ]
        profile = hour_of_day_profile(rows, field=field)
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

    @app.get("/api/guide")
    def guide():
        return {
            "pm2_5": PM25_BANDS,
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
  header { position:relative; overflow:hidden; height:clamp(150px, 22vw, 300px);
    background:linear-gradient(to top, #f7ca89 4%, #e4d1ab 18%, #cddcd7 38%, #c5e0e8 49%, #a1bcd1 82%);
    display:flex; align-items:center; justify-content:center; }
  header .cloud { position:absolute; bottom:0; left:50%; width:120%; height:auto;
    transform:translateX(-50%); pointer-events:none; }
  header .c-gold  { animation: sway-g 36s ease-in-out infinite alternate; }
  header .c-blue  { animation: sway-b 27s ease-in-out infinite alternate; }
  header .c-white { animation: sway-w 20s ease-in-out infinite alternate; }
  @keyframes sway-g { 0%,9%{transform:translateX(calc(-50% - 12px))} 91%,100%{transform:translateX(calc(-50% + 12px))} }
  @keyframes sway-b { 0%,9%{transform:translateX(calc(-50% - 20px))} 91%,100%{transform:translateX(calc(-50% + 20px))} }
  @keyframes sway-w { 0%,9%{transform:translateX(calc(-50% - 32px))} 91%,100%{transform:translateX(calc(-50% + 32px))} }
  @media (prefers-reduced-motion:reduce){ header .cloud{animation:none} }
  header img.logo { position:relative; z-index:2; height:clamp(92px, 12vw, 150px); width:auto; max-width:92%;
    filter:drop-shadow(0 1px 2px rgba(0,0,0,.12)); }
  .subbar { background:linear-gradient(90deg,var(--brand),var(--brand-purple)); color:#fff;
    padding:8px 20px; font-size:13px; text-align:center; }
  .controls { padding:14px 20px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  select { padding:6px 10px; font-size:14px; }
  .meta { color:#666; font-size:13px; }
  .card { background:#fff; margin:16px 20px; border:1px solid #e5e5e8; border-radius:8px; }
  .card h2 { font-size:14px; margin:0; padding:10px 14px; border-bottom:1px solid #eee; color:#444; }
  .chart { height:320px; }
  #map { height:360px; border-radius:0 0 8px 8px; }
  .legend { font-size:12px; color:#666; padding:6px 14px; }
  .sensorlist { display:flex; flex-direction:column; gap:2px; max-height:120px; overflow:auto;
    border:1px solid #ddd; border-radius:6px; padding:6px 10px; font-size:13px; min-width:200px; }
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
<div class="controls">
  <div><b>Sensors</b><div id="sensors" class="sensorlist"></div></div>
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
  <label><input type="checkbox" id="showsources" checked> 🏭 pollution sources</label>
  <label><input type="checkbox" id="showref" checked> 📍 EPA monitors</label>
</div>
<div class="card"><h2>Sensor map</h2><div id="map"></div>
  <div class="legend">PM2.5 µg/m³: <span style="color:#00e400">●</span> &lt;12
    <span style="color:#e0d000">●</span> &lt;35 <span style="color:#ff7e00">●</span> &lt;55
    <span style="color:#ff0000">●</span> &lt;150 <span style="color:#8f3f97">●</span> higher · click a marker to toggle</div>
</div>
<div class="card"><h2>Time series (hourly median) — red × = events, dashed = trend (single sensor)
  <span class="meta" id="trendinfo"></span></h2><div id="ts" class="chart"></div>
  <div class="meta" id="detail" style="padding:6px 14px">Click a point (or event ×) for detail.</div>
</div>
<div class="card"><h2>Time-of-day profile (median by local hour, ET)</h2><div id="diurnal" class="chart"></div></div>
<div class="card"><h2>Day vs. overnight compare</h2>
  <table id="cmp"><thead><tr><th>Sensor</th><th>Day (9-17)</th><th>Night (0-5)</th><th>Night/Day</th></tr></thead><tbody></tbody></table>
</div>
<div class="card"><h2>Health guide — what the levels mean</h2><div id="guide" class="guide"></div></div>
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
const COLORS = ['#3b2a6b','#e07b00','#1b9e77','#d62728','#7570b3','#17becf','#b8860b'];
let map, markers = {}, allSensors = [], GUIDE = null;

async function loadGuide(){
  GUIDE = await j('/api/guide');
  const bands = (arr, unit) => arr.map((b,i) => {
    const lo = i === 0 ? 0 : arr[i-1].max;
    const range = b.max == null ? `${lo}+` : `${lo}–${b.max}`;
    return `<span class="chip"><span class="sw" style="background:${b.color}"></span>${b.label} (${range}${unit})</span>`;
  }).join('');
  $('guide').innerHTML =
    '<b style="flex-basis:100%">PM2.5 (µg/m³)</b>' + bands(GUIDE.pm2_5, '') +
    '<b style="flex-basis:100%;margin-top:6px">VOC (relative index)</b>' + bands(GUIDE.voc, '') +
    `<div class="note">${GUIDE.voc_note}</div>`;
}
function bandShapes(field, yMax){
  const arr = field === 'pm2_5' ? GUIDE?.pm2_5 : field === 'voc' ? GUIDE?.voc : null;
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
  $('sensors').innerHTML = allSensors.map((x,i) =>
    `<label><input type="checkbox" value="${x.sensor_id}" ${i===0?'checked':''}> ${x.name}</label>`).join('');
  $('sensors').querySelectorAll('input').forEach(c => c.addEventListener('change', render));
  drawMap(allSensors);
  render();
}
function drawMap(sensors){
  if (!map){
    map = L.map('map').setView([38.35, -81.6], 8);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom:18, attribution:'© OpenStreetMap'}).addTo(map);
  }
  const pts = [];
  sensors.forEach(x => {
    if (x.lat == null || x.lon == null) return;
    L.circleMarker([x.lat, x.lon], {radius:9, color:'#333', weight:1, fillColor:x.color, fillOpacity:0.9})
      .bindPopup(`<b>${x.name}</b><br>latest PM2.5: ${x.latest_pm2_5 ?? '—'}<br>${x.count} readings`)
      .on('click', () => { const c = box(x.sensor_id); if(c){ c.checked = !c.checked; render(); } })
      .addTo(map);
    pts.push([x.lat, x.lon]);
  });
  if (pts.length) map.fitBounds(pts, {padding:[30,30], maxZoom:11});
  loadSources();
  loadReference();
}
let refLayer;
async function loadReference(){
  const data = await j('/api/reference-monitors');
  if (refLayer) refLayer.remove();
  refLayer = L.layerGroup();
  data.monitors.forEach(m => {
    if (m.lat == null || m.lon == null) return;
    L.marker([m.lat, m.lon], {icon: L.divIcon({className:'', html:'📍', iconSize:[20,20], iconAnchor:[10,20]})})
      .bindPopup(`<b>${m.name}</b> (${m.county} Co.)<br>EPA regulatory monitor`+
        `<br>2024 mean PM2.5: <b>${m.mean_pm25}</b> µg/m³ (${m.days} days)`+
        `<br><small>${m.citation||''}</small>`)
      .addTo(refLayer);
  });
  if ($('showref').checked) refLayer.addTo(map);
}
let sourceLayer;
async function loadSources(){
  const data = await j('/api/sources');
  if (sourceLayer) sourceLayer.remove();
  sourceLayer = L.layerGroup();
  data.sources.forEach(s => {
    if (s.lat == null || s.lon == null) return;
    L.marker([s.lat, s.lon], {icon: L.divIcon({className:'', html:'🏭',
      iconSize:[22,22], iconAnchor:[11,11]})})
      .bindPopup(`<b>${s.name}</b><br>${s.type}<br><i>${s.operator||''}</i>`+
        `<br><small>Documented public-record facility · ${s.citation||''}</small>`+
        `<br><small style="color:#a00">${data.disclaimer||''}</small>`)
      .addTo(sourceLayer);
  });
  if ($('showsources').checked) sourceLayer.addTo(map);
}

const box = id => $('sensors').querySelector(`input[value="${id}"]`);
const selected = () => [...$('sensors').querySelectorAll('input:checked')].map(c => c.value);
function range(){ const s=$('start').value, e=$('end').value;
  return (s?`&start=${s}`:'') + (e?`&end=${e}`:''); }

async function render(){
  const ids = selected(), field = $('field').value, rng = range();
  $('dl').href = ids.length ? `/api/export/${ids[0]}.csv` : '#';
  const tsTraces = [], diTraces = [];
  const series = await Promise.all(ids.map(id => j(`/api/series/${id}?field=${field}${rng}`)));
  series.forEach((s,i) => tsTraces.push({x:s.points.map(p=>p.ts), y:s.points.map(p=>p.value),
    mode:'lines', name:s.name, line:{color:COLORS[i%COLORS.length], width:1.3}}));
  if (ids.length === 1){
    const [ev, tr] = await Promise.all([
      j(`/api/events/${ids[0]}?field=${field}`),
      j(`/api/trend/${ids[0]}?field=${field}`),
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

  const dis = await Promise.all(ids.map(id => j(`/api/diurnal/${id}?field=${field}${rng}`)));
  dis.forEach((d,i) => diTraces.push({x:d.hours.map(h=>h.hour), y:d.hours.map(h=>h.median),
    mode:'lines+markers', name:d.name, line:{color:COLORS[i%COLORS.length]}}));
  Plotly.newPlot('diurnal', diTraces, {margin:{t:10,r:10,b:40,l:45},
    xaxis:{title:'hour of day (ET)', dtick:2}, yaxis:{title:field}, legend:{orientation:'h'}},
    {responsive:true, displayModeBar:false});

  const cmp = await j(`/api/compare?sensors=${ids.join(',')}&field=${field}`);
  $('cmp').querySelector('tbody').innerHTML = cmp.sensors.map(s =>
    `<tr><td>${s.name}</td><td>${s.day ?? '—'}</td><td>${s.night ?? '—'}</td>
     <td><b>${s.night_day_ratio ?? '—'}</b></td></tr>`).join('');
}
$('field').addEventListener('change', render);
$('start').addEventListener('change', render);
$('end').addEventListener('change', render);
$('clear').addEventListener('click', () => { $('start').value=''; $('end').value=''; render(); });
$('showsources').addEventListener('change', e => {
  if (!sourceLayer) return;
  e.target.checked ? sourceLayer.addTo(map) : sourceLayer.remove();
});
$('showref').addEventListener('change', e => {
  if (!refLayer) return;
  e.target.checked ? refLayer.addTo(map) : refLayer.remove();
});
loadGuide().then(loadSensors);
</script>
</body>
</html>"""
