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
from fastapi.responses import HTMLResponse

from airwv.analysis import detrend_events, hour_of_day_profile
from airwv.storage import Store

# Fields safe to chart (mirror the Reading schema).
FIELDS = ["pm2_5", "pm1_0", "pm10", "voc", "aqi", "temperature", "humidity", "pressure"]


def _parse_date(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _pm25_color(value: float | None) -> str:
    """Rough EPA-style color for a PM2.5 concentration (µg/m³)."""
    if value is None:
        return "#9e9e9e"
    for limit, color in ((12, "#00e400"), (35, "#ffff00"), (55, "#ff7e00"),
                         (150, "#ff0000"), (250, "#8f3f97")):
        if value < limit:
            return color
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

    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML

    return app


def _default_store() -> Store:
    url = os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite"
    return Store(url)


app = create_app(_default_store())


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AirWV Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background:#f7f7f8; color:#1a1a1a; }
  header { background:#3b2a6b; color:#fff; padding:14px 20px; }
  header h1 { margin:0; font-size:20px; }
  header p { margin:4px 0 0; opacity:.8; font-size:13px; }
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
</style>
</head>
<body>
<header>
  <h1>AirWV — West Virginia Air Quality</h1>
  <p>Community sensor data. Map colored by latest PM2.5; times in US Eastern.</p>
</header>
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
</div>
<div class="card"><h2>Sensor map</h2><div id="map"></div>
  <div class="legend">PM2.5 µg/m³: <span style="color:#00e400">●</span> &lt;12
    <span style="color:#e0d000">●</span> &lt;35 <span style="color:#ff7e00">●</span> &lt;55
    <span style="color:#ff0000">●</span> &lt;150 <span style="color:#8f3f97">●</span> higher · click a marker to toggle</div>
</div>
<div class="card"><h2>Time series (hourly median) — red × = events, dashed = trend (single sensor)
  <span class="meta" id="trendinfo"></span></h2><div id="ts" class="chart"></div></div>
<div class="card"><h2>Time-of-day profile (median by local hour, ET)</h2><div id="diurnal" class="chart"></div></div>
<div class="card"><h2>Day vs. overnight compare</h2>
  <table id="cmp"><thead><tr><th>Sensor</th><th>Day (9-17)</th><th>Night (0-5)</th><th>Night/Day</th></tr></thead><tbody></tbody></table>
</div>
<script>
const $ = id => document.getElementById(id);
const j = async u => (await fetch(u)).json();
const COLORS = ['#3b2a6b','#e07b00','#1b9e77','#d62728','#7570b3','#17becf','#b8860b'];
let map, markers = {}, allSensors = [];

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
}
const box = id => $('sensors').querySelector(`input[value="${id}"]`);
const selected = () => [...$('sensors').querySelectorAll('input:checked')].map(c => c.value);
function range(){ const s=$('start').value, e=$('end').value;
  return (s?`&start=${s}`:'') + (e?`&end=${e}`:''); }

async function render(){
  const ids = selected(), field = $('field').value, rng = range();
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
      mode:'markers', name:'event', marker:{color:'#e00', symbol:'x', size:8}});
    const pts = series[0].points;
    if (tr.first != null && pts.length){
      tsTraces.push({x:[pts[0].ts, pts[pts.length-1].ts], y:[tr.first, tr.last],
        mode:'lines', name:'trend', line:{dash:'dash', color:'#111', width:2}});
    }
    $('trendinfo').textContent = tr.direction === 'insufficient' ? '' :
      `trend: ${tr.direction}${tr.watch ? ' ⚠ watch' : ''} · Δ${tr.pct_change}% over period (r=${tr.r})`;
  } else { $('trendinfo').textContent = ''; }
  Plotly.newPlot('ts', tsTraces, {margin:{t:10,r:10,b:40,l:45}, yaxis:{title:field},
    legend:{orientation:'h'}}, {responsive:true, displayModeBar:false});

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
loadSensors();
</script>
</body>
</html>"""
