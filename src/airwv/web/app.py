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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from airwv.analysis import detrend_events, hour_of_day_profile
from airwv.export_utils import readings_to_csv
from airwv.reporting import DOMAINS, client_ip, ip_hash, jitter, load_facility_triggers, screen
from airwv.storage import Store

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class ReportIn(BaseModel):
    domain: str
    category: str = ""
    description: str = ""
    lat: float
    lon: float
    observed_at: str | None = None
    suspected_org: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    website: str = ""      # honeypot — bots fill it, humans never see it
    elapsed_ms: int = 0    # time on form; near-zero = a bot


class FeedbackIn(BaseModel):
    kind: str = "idea"     # bug | idea | question
    message: str
    page: str | None = None
    contact: str | None = None
    website: str = ""      # honeypot

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


def _airnow_meta() -> dict[str, str]:
    """AQSID -> friendly monitor name, from the accumulated AirNow metadata."""
    try:
        import json

        cache = os.environ.get("AIRWV_INDEX_CACHE", "").strip() or "data/sensor_index_map.json"
        path = Path(cache).with_name("airnow_monitors.json")
        return {k: (v.get("name") or k) for k, v in json.loads(path.read_text()).items()}
    except Exception:
        return {}


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
    airnow_names = _airnow_meta()
    facility_triggers = load_facility_triggers(Path(__file__).parent.parent / "data" / "sources.json")

    @app.get("/api/sensors")
    def sensors():
        # SQL aggregation instead of loading every row (was O(all rows) per sensor).
        coverage = store.sensor_coverage()
        latest = store.latest_value_per_sensor("pm2_5")
        # AirNow is the LIVE reference layer; OpenAQ + AirData daily are archive/history.
        ref_ids = set(store.sensor_ids_by_source("airnow"))
        ref_coords = store.coords_from_readings("airnow")
        archive_ids = (set(store.sensor_ids_by_source("epa_airdata"))
                       | set(store.sensor_ids_by_source("openaq")))
        out = []
        for sid, cov in coverage.items():
            if sid in archive_ids:
                continue  # historical/archive reference — used for validation, not the live map
            is_ref = sid in ref_ids
            if is_ref:  # AirNow reference monitor — coords on the readings
                lat, lon = ref_coords.get(sid, (None, None))
                name = airnow_names.get(sid) or f"AirNow {sid}"
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

    # -- community reports & site feedback (see docs/COMMUNITY-REPORTING.md) --

    def _public_report(r) -> dict:
        """Public projection: jittered coords, no private fields, org only if approved."""
        lat, lon = jitter(r.lat, r.lon, r.id)
        return {
            "id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
            "observed_at": r.observed_at.isoformat() if r.observed_at else None,
            "domain": r.domain, "category": r.category, "description": r.description,
            "lat": lat, "lon": lon, "area_label": r.area_label, "stage": r.stage,
            "verified": r.stage == "confirmed", "verified_by": r.verified_by,
            "org": r.suspected_org if r.org_public else None,
        }

    @app.post("/api/reports")
    def create_report(body: ReportIn, request: Request):
        if body.website.strip():
            raise HTTPException(status_code=400, detail="rejected")          # honeypot
        if body.elapsed_ms and body.elapsed_ms < 1500:
            raise HTTPException(status_code=400, detail="submitted too fast")  # bot
        if body.domain not in DOMAINS:
            raise HTTPException(status_code=400, detail=f"unknown domain {body.domain!r}")
        iph = ip_hash(client_ip(request))
        if store.count_reports_since(iph, minutes=60) >= 5:
            raise HTTPException(status_code=429, detail="too many reports — try again later")
        stage, reason = screen(body.description, body.domain, body.suspected_org, facility_triggers)
        rid = store.add_report(
            domain=body.domain, category=body.category[:64], description=(body.description or "")[:2000],
            lat=body.lat, lon=body.lon, observed_at=_parse_dt(body.observed_at),
            suspected_org=(body.suspected_org or None), contact_email=(body.contact_email or None),
            contact_phone=(body.contact_phone or None), ip_hash=iph, stage=stage, screen_reason=reason)
        return {"id": rid, "stage": stage,
                "message": ("Thanks — a maintainer will review this shortly."
                            if stage == "held" else "Thanks — your report is on the map.")}

    @app.get("/api/reports")
    def list_reports(domain: str | None = None):
        return {"results": [_public_report(r) for r in store.published_reports(domain=domain)]}

    @app.post("/api/reports/{report_id}/flag")
    def flag_report(report_id: int):
        n = store.flag_report(report_id)
        if n < 0:
            raise HTTPException(status_code=404, detail="no such report")
        return {"flags": n}

    @app.post("/api/feedback")
    def create_feedback(body: FeedbackIn, request: Request):
        if body.website.strip():
            raise HTTPException(status_code=400, detail="rejected")          # honeypot
        if not (body.message or "").strip():
            raise HTTPException(status_code=400, detail="message required")
        fid = store.add_feedback(
            kind=(body.kind if body.kind in ("bug", "idea", "question") else "idea"),
            message=body.message[:2000], page=(body.page or None), contact=(body.contact or None),
            ip_hash=ip_hash(client_ip(request)))
        return {"id": fid, "message": "Thanks for the feedback!"}

    def _parse_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

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

    @app.get("/api/wind-roses")
    def wind_roses():
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "wind_roses.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"stations": []}

    @app.get("/api/reference-monitors")
    def reference_monitors():
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "reference_monitors.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"note": data.get("note"), "monitors": data.get("monitors", [])}
        except Exception:
            return {"note": "", "monitors": []}

    _validate_cache: dict = {}

    @app.get("/api/validate")
    def validate(correct: bool = False):
        """Community sensors vs their nearest regulatory reference monitor.

        Aggregates years of data, so it's cached for 30 min (it doesn't change
        minute-to-minute) rather than recomputed on every page load.
        """
        import time as _t

        from airwv.ingest import run_validate

        hit = _validate_cache.get(correct)
        if hit and _t.time() - hit[0] < 1800:
            return hit[1]

        results = run_validate(store=store, coords=coords, correct=correct)
        for r in results:
            r["sensor_name"] = names.get(r["sensor"], r["sensor"])
        note = ("Each community sensor is paired with its nearest regulatory reference "
                "monitor (EPA AirNow / AirData / OpenAQ), correlating daily PM2.5 over "
                "their full overlap (up to years). r = correlation (1.0 = perfect "
                "tracking); bias = sensor − reference (µg/m³). High r validates the "
                "sensor; a wild bias or low r flags a problem.")
        if correct:
            note += (" PM2.5 is EPA-corrected (Barkjohn 2021), which pulls raw "
                     "PurpleAir's high bias down toward reference grade.")
        resp = {"results": results, "corrected": correct, "note": note}
        _validate_cache[correct] = (_t.time(), resp)
        return resp

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
    def index(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="dashboard.html")

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


def _default_store() -> Store:
    url = os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite"
    return Store(url)


app = create_app(_default_store())
