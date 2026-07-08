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
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from airwv.analysis import hour_of_day_profile
from airwv.storage import Store

# Fields safe to chart (mirror the Reading schema).
FIELDS = ["pm2_5", "pm1_0", "pm10", "voc", "aqi", "temperature", "humidity", "pressure"]


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


def create_app(store: Store) -> FastAPI:
    app = FastAPI(title="AirWV Dashboard")
    names = _index_to_name()

    @app.get("/api/sensors")
    def sensors():
        out = []
        for sid in store.distinct_sensor_ids():
            rows = store.readings_for_sensor(sid)
            if not rows:
                continue
            out.append({
                "sensor_id": sid,
                "name": names.get(sid, sid),
                "count": len(rows),
                "first_ts": rows[0].ts.isoformat(),
                "last_ts": rows[-1].ts.isoformat(),
            })
        out.sort(key=lambda s: s["name"])
        return out

    def _check_field(field: str):
        if field not in FIELDS:
            raise HTTPException(status_code=400, detail=f"unknown field {field!r}")

    @app.get("/api/series/{sensor_id}")
    def series(sensor_id: str, field: str = "pm2_5"):
        _check_field(field)
        buckets: dict = defaultdict(list)
        for r in store.readings_for_sensor(sensor_id):
            value = getattr(r, field)
            if value is not None:
                buckets[r.ts.replace(minute=0, second=0, microsecond=0)].append(value)
        points = [
            {"ts": ts.isoformat(), "value": round(statistics.median(vals), 1)}
            for ts, vals in sorted(buckets.items())
        ]
        return {"sensor_id": sensor_id, "field": field, "points": points}

    @app.get("/api/diurnal/{sensor_id}")
    def diurnal(sensor_id: str, field: str = "voc"):
        _check_field(field)
        profile = hour_of_day_profile(store.readings_for_sensor(sensor_id), field=field)
        return {
            "sensor_id": sensor_id,
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
  .chart { height:340px; }
</style>
</head>
<body>
<header>
  <h1>AirWV — West Virginia Air Quality</h1>
  <p>Community sensor data. Times shown in US Eastern.</p>
</header>
<div class="controls">
  <label>Sensor <select id="sensor"></select></label>
  <label>Metric <select id="field">
    <option value="pm2_5">PM2.5</option>
    <option value="voc">VOC</option>
    <option value="pm10">PM10</option>
    <option value="pm1_0">PM1.0</option>
    <option value="temperature">Temperature</option>
    <option value="humidity">Humidity</option>
  </select></label>
  <span class="meta" id="meta"></span>
</div>
<div class="card"><h2>Time series (hourly median)</h2><div id="ts" class="chart"></div></div>
<div class="card"><h2>Time-of-day profile (median by local hour)</h2><div id="diurnal" class="chart"></div></div>
<script>
const $ = id => document.getElementById(id);
async function j(u){ const r = await fetch(u); return r.json(); }

async function loadSensors(){
  const s = await j('/api/sensors');
  const sel = $('sensor');
  sel.innerHTML = s.map(x => `<option value="${x.sensor_id}" data-count="${x.count}"
      data-first="${x.first_ts}" data-last="${x.last_ts}">${x.name}</option>`).join('');
  if (s.length) render();
}
function meta(){
  const o = $('sensor').selectedOptions[0]; if(!o) return;
  $('meta').textContent = `${o.dataset.count} readings · ${(''+o.dataset.first).slice(0,10)} → ${(''+o.dataset.last).slice(0,10)}`;
}
async function render(){
  meta();
  const sid = $('sensor').value, field = $('field').value;
  const ser = await j(`/api/series/${sid}?field=${field}`);
  Plotly.newPlot('ts', [{x: ser.points.map(p=>p.ts), y: ser.points.map(p=>p.value),
    mode:'lines', line:{color:'#3b2a6b', width:1}}],
    {margin:{t:10,r:10,b:40,l:45}, yaxis:{title:field}}, {responsive:true, displayModeBar:false});
  const di = await j(`/api/diurnal/${sid}?field=${field}`);
  Plotly.newPlot('diurnal', [{x: di.hours.map(h=>h.hour), y: di.hours.map(h=>h.median),
    type:'bar', marker:{color:'#7a5cc0'}}],
    {margin:{t:10,r:10,b:40,l:45}, xaxis:{title:'hour of day (ET)', dtick:2}, yaxis:{title:field}},
    {responsive:true, displayModeBar:false});
}
$('sensor').addEventListener('change', render);
$('field').addEventListener('change', render);
loadSensors();
</script>
</body>
</html>"""
