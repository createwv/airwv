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

import base64
import os
import secrets
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from airwv.analysis import detrend_events, hour_of_day_profile
from airwv.export_utils import readings_to_csv
from airwv.notify.chat import chat_notifier_from_env
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


class ModerateIn(BaseModel):
    action: str            # confirm | publish | keep | remove | approve_org
    mod_note: str | None = None
    verified_by: str | None = None


class FeedbackStatusIn(BaseModel):
    status: str            # new | triaged | done


class FieldReadingIn(BaseModel):
    submitter: str
    medium: str = "air"                    # air | water | soil | other
    parameter: str                         # VOC, conductivity, pH, PM2.5, …
    value: float
    unit: str = ""
    method: str | None = None              # instrument / how measured
    lat: float
    lon: float
    area_label: str | None = None
    notes: str | None = None
    observed_at: str | None = None         # ISO
    photo: str | None = None               # optional base64 data URL of the meter


FIELD_PHOTO_DIR = Path(os.environ.get("AIRWV_FIELD_PHOTOS", "data/field_photos"))


def _save_field_photo(fid: int, data_url: str) -> str | None:
    """Decode a base64 data-URL image (meter photo) and save it as <id>.<ext>. Best-effort."""
    try:
        header, b64 = data_url.split(",", 1) if "," in data_url else ("", data_url)
        raw = base64.b64decode(b64)
        if not raw or len(raw) > 8 * 1024 * 1024:   # cap 8 MB
            return None
        ext = "png" if "image/png" in header else "jpg"
        FIELD_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{fid}.{ext}"
        (FIELD_PHOTO_DIR / name).write_bytes(raw)
        return name
    except Exception:
        return None


class EventIn(BaseModel):
    title: str
    medium: str = "air"                    # air | water | soil | other
    kind: str = "other"                    # fire | explosion | haze | spill | odor | other
    region: str | None = None
    lat: float | None = None
    lon: float | None = None
    start_ts: str | None = None            # ISO
    end_ts: str | None = None
    description: str | None = None
    origin: str | None = None              # likely/suspected/known cause
    scope: str | None = None               # Local | Regional | Multi-state | Continental
    regions_affected: str | None = None
    captured: bool = False
    sensor_ids: list[str] = []
    source_refs: list[str] = []            # facility names (link to /sources)
    report_ids: list[int] = []
    sources: list = []                     # [{"label","url"}, ...] citations
    status: str = "published"              # published | draft | archived


def _require_admin(request: Request) -> bool:
    """Gate admin endpoints on AIRWV_ADMIN_TOKEN (X-Admin-Token header). Fails closed:
    if no token is configured, admin is disabled entirely."""
    token = os.environ.get("AIRWV_ADMIN_TOKEN", "").strip()
    given = request.headers.get("x-admin-token", "")
    if not token or not secrets.compare_digest(given, token):
        raise HTTPException(status_code=401, detail="admin token required")
    return True

# Fields safe to chart (mirror the Reading schema).
FIELDS = ["pm2_5", "pm1_0", "pm10", "voc", "ozone", "aqi", "temperature", "humidity", "pressure"]


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

# EPA ozone AQI categories (8-hour, ppb upper bounds; a guide for the hourly AirNow value).
OZONE_BANDS = [
    {"max": 54.0, "label": "Good", "color": "#00e400"},
    {"max": 70.0, "label": "Moderate", "color": "#ffff00"},
    {"max": 85.0, "label": "Unhealthy for sensitive groups", "color": "#ff7e00"},
    {"max": 105.0, "label": "Unhealthy", "color": "#ff0000"},
    {"max": 200.0, "label": "Very unhealthy", "color": "#8f3f97"},
    {"max": None, "label": "Hazardous", "color": "#7e0023"},
]


def _pm25_color(value: float | None) -> str:
    """EPA-style color for a PM2.5 concentration (µg/m³)."""
    if value is None:
        return "#9e9e9e"
    for band in PM25_BANDS:
        if band["max"] is None or value <= band["max"]:
            return band["color"]
    return "#7e0023"


def _ozone_color(value: float | None) -> str:
    """EPA-style color for an ozone reading (ppb)."""
    if value is None:
        return "#9e9e9e"
    for band in OZONE_BANDS:
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
    notifier = chat_notifier_from_env()   # Slack/Discord webhooks (env); disabled if unset

    @app.get("/api/sensors")
    def sensors():
        # SQL aggregation instead of loading every row (was O(all rows) per sensor).
        coverage = store.sensor_coverage()
        latest = store.latest_value_per_sensor("pm2_5")
        latest_o3 = store.latest_value_per_sensor("ozone")   # ppb — AirNow reference monitors
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
            # Display-time sanity: a sustained reading this high is a stuck/broken sensor
            # (real ambient PM2.5 doesn't sit above ~1000). Don't paint a false "hazardous"
            # value — grey it and flag it instead of hiding the sensor.
            malfunction = latest_pm is not None and latest_pm > 1000
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
                "latest_pm2_5": None if malfunction else latest_pm,
                "color": "#9e9e9e" if malfunction else _pm25_color(latest_pm),
                "flag": "malfunction" if malfunction else None,
                "latest_ozone": latest_o3.get(sid),
                "ozone_color": _ozone_color(latest_o3.get(sid)),
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
    def create_report(body: ReportIn, request: Request, background: BackgroundTasks):
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
        if notifier.enabled:   # push to Slack/Discord after the response is sent
            background.add_task(notifier.notify_report, domain=body.domain,
                                category=body.category, description=body.description,
                                stage=stage, lat=body.lat, lon=body.lon)
        return {"id": rid, "stage": stage,
                "message": ("Thanks — a maintainer will review this shortly."
                            if stage == "held" else "Thanks — your report is on the map.")}

    @app.get("/api/reports")
    def list_reports(domain: str | None = None):
        return {"results": [_public_report(r) for r in store.published_reports(domain=domain)]}

    def _event_dict(e) -> dict:
        return {
            "id": e.id, "title": e.title, "medium": e.medium, "kind": e.kind, "region": e.region,
            "lat": e.lat, "lon": e.lon,
            "start_ts": e.start_ts.isoformat() if e.start_ts else None,
            "end_ts": e.end_ts.isoformat() if e.end_ts else None,
            "description": e.description, "captured": e.captured,
            "origin": e.origin, "scope": e.scope, "regions_affected": e.regions_affected,
            "sensor_ids": e.sensor_ids or [], "source_refs": e.source_refs or [],
            "report_ids": e.report_ids or [], "sources": e.sources or [], "status": e.status,
        }

    @app.get("/api/events")   # curated events list (distinct from /api/events/{sensor_id})
    def list_events():
        return {"results": [_event_dict(e) for e in store.published_events()]}

    @app.post("/api/reports/{report_id}/flag")
    def flag_report(report_id: int):
        n = store.flag_report(report_id)
        if n < 0:
            raise HTTPException(status_code=404, detail="no such report")
        return {"flags": n}

    @app.post("/api/feedback")
    def create_feedback(body: FeedbackIn, request: Request, background: BackgroundTasks):
        if body.website.strip():
            raise HTTPException(status_code=400, detail="rejected")          # honeypot
        if not (body.message or "").strip():
            raise HTTPException(status_code=400, detail="message required")
        kind = body.kind if body.kind in ("bug", "idea", "question") else "idea"
        fid = store.add_feedback(
            kind=kind, message=body.message[:2000], page=(body.page or None),
            contact=(body.contact or None), ip_hash=ip_hash(client_ip(request)))
        if notifier.enabled:
            background.add_task(notifier.notify_feedback, kind=kind, message=body.message,
                                page=body.page, contact=body.contact)
        return {"id": fid, "message": "Thanks for the feedback!"}

    # -- admin / moderation (token-gated via AIRWV_ADMIN_TOKEN) -------------

    def _admin_report(r) -> dict:
        return {
            "id": r.id, "created_at": r.created_at, "observed_at": r.observed_at,
            "domain": r.domain, "category": r.category, "description": r.description,
            "lat": r.lat, "lon": r.lon, "area_label": r.area_label, "stage": r.stage,
            "screen_reason": r.screen_reason, "flags_count": r.flags_count, "verified_by": r.verified_by,
            "suspected_org": r.suspected_org, "org_public": r.org_public,
            "photo_ok": r.photo_ok, "contact_email": r.contact_email, "contact_phone": r.contact_phone,
            "ip_hash": r.ip_hash, "mod_note": r.mod_note,
        }

    @app.get("/api/admin/reports")
    def admin_reports(status: str = "held", _: bool = Depends(_require_admin)):
        return {"results": [_admin_report(r) for r in store.reports_for_admin(status)]}

    @app.post("/api/admin/reports/{report_id}")
    def admin_moderate(report_id: int, body: ModerateIn, _: bool = Depends(_require_admin)):
        if not store.moderate_report(report_id, body.action, mod_note=body.mod_note,
                                     verified_by=body.verified_by):
            raise HTTPException(status_code=404, detail="no such report")
        return {"ok": True}

    @app.get("/api/admin/feedback")
    def admin_feedback(status: str | None = None, _: bool = Depends(_require_admin)):
        return {"results": [{"id": f.id, "created_at": f.created_at, "kind": f.kind,
                             "message": f.message, "page": f.page, "contact": f.contact,
                             "status": f.status} for f in store.feedback_for_admin(status)]}

    @app.post("/api/admin/feedback/{feedback_id}")
    def admin_feedback_update(feedback_id: int, body: FeedbackStatusIn, _: bool = Depends(_require_admin)):
        if not store.update_feedback(feedback_id, body.status):
            raise HTTPException(status_code=404, detail="no such feedback")
        return {"ok": True}

    @app.post("/api/admin/notify-test")
    def admin_notify_test(_: bool = Depends(_require_admin)):
        if not notifier.enabled:
            return {"sent": False, "detail": "no webhook configured — set "
                    "AIRWV_SLACK_WEBHOOK_URL and/or AIRWV_DISCORD_WEBHOOK_URL, then restart"}
        notifier.send("✅ AirWV test notification",
                      ["If you can see this, new report + feedback alerts are wired up."],
                      link_label="Open admin", link_url=notifier.admin_link, username="AirWV")
        return {"sent": True, "slack": bool(notifier.slack_url), "discord": bool(notifier.discord_url)}

    @app.get("/api/admin/events")
    def admin_events(_: bool = Depends(_require_admin)):
        return {"results": [_event_dict(e) for e in store.events_for_admin()]}

    @app.post("/api/admin/events")
    def admin_event_create(body: EventIn, _: bool = Depends(_require_admin)):
        eid = store.add_event(
            title=body.title[:200], medium=body.medium, kind=body.kind, region=(body.region or None),
            lat=body.lat, lon=body.lon, start_ts=_parse_dt(body.start_ts), end_ts=_parse_dt(body.end_ts),
            description=(body.description or None), origin=(body.origin or None),
            scope=(body.scope or None), regions_affected=(body.regions_affected or None),
            captured=body.captured, sensor_ids=[str(s) for s in body.sensor_ids],
            source_refs=body.source_refs, report_ids=body.report_ids,
            sources=body.sources, status=body.status)
        return {"id": eid}

    @app.post("/api/admin/events/{event_id}")
    def admin_event_update(event_id: int, body: EventIn, action: str | None = None,
                           _: bool = Depends(_require_admin)):
        if action == "delete":
            return {"ok": store.delete_event(event_id)}
        ok = store.update_event(
            event_id, title=body.title[:200], medium=body.medium, kind=body.kind, region=body.region,
            lat=body.lat, lon=body.lon, start_ts=_parse_dt(body.start_ts), end_ts=_parse_dt(body.end_ts),
            description=body.description, origin=body.origin, scope=body.scope,
            regions_affected=body.regions_affected, captured=body.captured,
            sensor_ids=[str(s) for s in body.sensor_ids], source_refs=body.source_refs,
            report_ids=body.report_ids, sources=body.sources, status=body.status)
        if not ok:
            raise HTTPException(status_code=404, detail="no such event")
        return {"ok": True}

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="admin.html",
                                          context={"mode": "admin"})

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
            "ozone": OZONE_BANDS,
            "voc_note": "VOC is a relative gas index (uncalibrated, drifts per sensor) — trends and comparisons at one sensor are meaningful; absolute cross-sensor values are not.",
            "ozone_note": "Ozone (ppb) comes from EPA AirNow reference monitors only — community sensors don't measure it. Bands are the 8-hour AQI categories as a rough guide for the hourly value.",
        }

    @app.get("/api/export/{sensor_id}.csv")
    def export_csv(sensor_id: str):
        csv_text = readings_to_csv(store.readings_for_sensor(sensor_id))
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="airwv_{sensor_id}.csv"'},
        )

    @app.get("/api/water/sites")
    def water_sites():
        return {"sites": store.water_sites()}

    @app.get("/api/water/series/{site_id}")
    def water_series(site_id: str, parameter: str = "ph", days: int = 30):
        since = datetime.utcnow() - timedelta(days=days)   # stored ts is naive UTC
        return {"site_id": site_id, "parameter": parameter,
                "points": store.water_series(site_id, parameter, since=since)}

    @app.get("/water", response_class=HTMLResponse)
    def water_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="water.html",
                                          context={"mode": "water"})

    # -- field readings (trained-scientist spot checks; submission is admin-gated) --
    def _field_public(fr) -> dict:
        return {
            "id": fr.id, "submitter": fr.submitter, "medium": fr.medium, "parameter": fr.parameter,
            "value": fr.value, "unit": fr.unit, "method": fr.method,
            "lat": fr.lat, "lon": fr.lon, "area_label": fr.area_label, "notes": fr.notes,
            "observed_at": fr.observed_at.isoformat() if fr.observed_at else None,
            "created_at": fr.created_at.isoformat() if fr.created_at else None,
            "has_photo": bool(fr.photo_path), "verified_by": fr.verified_by,
        }

    @app.get("/api/field-readings")
    def list_field_readings():
        return {"results": [_field_public(fr) for fr in store.published_field_readings()]}

    @app.post("/api/field-readings")
    def create_field_reading(body: FieldReadingIn, _: bool = Depends(_require_admin)):
        fid = store.add_field_reading(
            submitter=(body.submitter[:120] or "field team"), medium=body.medium,
            parameter=body.parameter[:64], value=body.value, unit=body.unit[:32],
            method=(body.method or None), lat=body.lat, lon=body.lon,
            area_label=(body.area_label or None), notes=(body.notes or None),
            observed_at=_parse_dt(body.observed_at), verified_by=(body.submitter[:120] or None),
            status="published")
        if body.photo:
            name = _save_field_photo(fid, body.photo)
            if name:
                store.set_field_reading(fid, photo_path=name)
        return {"id": fid}

    @app.get("/api/field-readings/{fid}/photo")
    def field_photo(fid: int):
        fr = store.get_field_reading(fid)
        if not fr or fr.status != "published" or not fr.photo_path:
            raise HTTPException(status_code=404, detail="no photo")
        path = FIELD_PHOTO_DIR / fr.photo_path
        if not path.exists():
            raise HTTPException(status_code=404, detail="missing file")
        return FileResponse(path)

    @app.get("/field", response_class=HTMLResponse)
    def field_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="field.html",
                                          context={"mode": "field"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="overview.html",
                                          context={"mode": "overview"})

    @app.get("/analysis", response_class=HTMLResponse)
    def analysis(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="dashboard.html",
                                          context={"mode": "analysis"})

    @app.get("/learn", response_class=HTMLResponse)
    def learn(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="learn.html",
                                          context={"mode": "learn"})

    @app.get("/events", response_class=HTMLResponse)
    def events_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="events.html",
                                          context={"mode": "events"})

    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(request: Request):
        # Google Street View Static API key (optional) — front-of-business photos when set.
        return TEMPLATES.TemplateResponse(request=request, name="sources.html",
                                          context={"mode": "sources",
                                                   "sv_key": os.environ.get("AIRWV_GOOGLE_MAPS_KEY", "")})

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="about.html",
                                          context={"mode": "about"})

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app


def _default_store() -> Store:
    url = os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite"
    return Store(url)


app = create_app(_default_store())
