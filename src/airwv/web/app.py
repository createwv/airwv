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
import html
import math
import os
import re
import secrets
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from airwv import dumps
from airwv.alerts import RECORD_RULES, is_record_sub
from airwv.analysis import detrend_events, hour_of_day_profile
from airwv.export_utils import (query_records_to_csv, query_series_to_csv, query_sites_to_csv,
                                readings_to_csv)
from airwv.notify.chat import chat_notifier_from_env
from airwv.notify.email import email_enabled, send_email
from airwv.reporting import DOMAINS, client_ip, ip_hash, jitter, load_facility_triggers, screen
from airwv.storage import Store
from airwv.storage.readmodel import measured_refs
from airwv.web.public_api import PublicDataMiddleware
from airwv.wvgeo import WV_COUNTY_CENTROID, county_names

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _asset_version() -> str:
    """A cache-busting stamp for static assets — the newest static-file mtime. Changes on
    every deploy (git pull touches the files + the app restarts), so ?v=<stamp> makes
    browsers/Cloudflare fetch fresh CSS/JS immediately instead of serving a stale copy."""
    static = Path(__file__).parent / "static"
    try:
        return str(int(max(p.stat().st_mtime for p in static.glob("*.*"))))
    except Exception:
        return "1"


TEMPLATES.env.globals["asset_v"] = _asset_version()

VENDOR_DIR = Path(__file__).parent / "static" / "vendor"


def _vendor(name: str, cdn: str) -> str:
    """URL for a third-party lib: the pinned self-hosted copy in static/vendor/ when it's
    there (see static/vendor/README.md), else the CDN — so a checkout without the copies
    still works."""
    return f"/static/vendor/{name}" if (VENDOR_DIR / name).is_file() else cdn


TEMPLATES.env.globals["vendor"] = _vendor


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    """``bbox=west,south,east,north`` (lon/lat degrees, the GeoJSON/OGC order) → a tuple,
    or None when absent. 400 on anything malformed."""
    if not bbox:
        return None
    try:
        w, s, e, n = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox is west,south,east,north")
    if w > e or s > n:
        raise HTTPException(status_code=400, detail="bbox is west,south,east,north")
    return w, s, e, n


def _in_bbox(lat, lon, box: tuple[float, float, float, float]) -> bool:
    return lat is not None and lon is not None and box[0] <= lon <= box[2] and box[1] <= lat <= box[3]


# Heavy, slow-changing JSON (hourly collectors, monthly layer refreshes): let browsers and
# Cloudflare reuse it briefly instead of refetching megabytes on every page view.
_SHORT_CACHE = {"Cache-Control": "public, max-age=60"}
_LAYER_CACHE = {"Cache-Control": "public, max-age=600"}

# site kinds of the read model's `site` view -> the plain label a site page shows
# (docs/GLOSSARY.md); each has a page at /site/<kind>/<ref>
SITE_KINDS = {
    "well": "Gas well", "facility": "Facility", "permit": "Permit", "spill_report": "Reported spill",
    "water_system": "Water system", "reference_monitor": "Monitor", "sensor": "Sensor",
    "gauge": "Gauge", "sample_site": "Sample site", "data_center": "Data center",
}

# record kinds of the `record` view -> what a site page calls them
SITE_RECORD_LABELS = {
    "orphan": "Orphan well (state list)", "violation": "EPA ECHO violation status",
    "compliance_status": "EPA ECHO compliance status", "impaired_303d": "Receiving stream impaired (303(d) list) for",
    "permit_stage": "Permit stage", "spill": "Reported to the National Response Center",
    "health_violation": "Health-based drinking-water violation", "project_status": "Project status",
}


def site_page_url(kind: str, ref) -> str:
    """The site page of one site: ``/site/<kind>/<ref>`` (ref URL-quoted)."""
    return f"/site/{kind}/{quote(str(ref), safe='')}"


class ReportReadingIn(BaseModel):
    """An optional structured measurement a resident attaches to a report."""
    parameter: str                         # VOC, conductivity, pH, PM2.5, …
    value: float
    unit: str = ""
    method: str | None = None              # instrument / how measured


class ReportIn(BaseModel):
    domain: str
    category: str = ""
    description: str = ""
    lat: float
    lon: float
    area_label: str | None = None          # coarse place name (reverse-geocoded, not a street address)
    observed_at: str | None = None
    suspected_org: str | None = None       # legacy/admin only — no longer collected from the public form
    contact_name: str | None = None        # PRIVATE — follow-up only
    contact_email: str | None = None
    contact_phone: str | None = None
    readings: list[ReportReadingIn] = []   # optional structured measurements
    photo: str | None = None               # optional base64 data URL (meter or scene)
    website: str = ""      # honeypot — bots fill it, humans never see it
    elapsed_ms: int = 0    # time on form; near-zero = a bot


class FeedbackIn(BaseModel):
    kind: str = "idea"     # bug | idea | question
    message: str
    page: str | None = None
    contact: str | None = None
    website: str = ""      # honeypot


class ModerateIn(BaseModel):
    action: str            # confirm | publish | keep | remove | approve_org | approve_photo | reject_photo
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
REPORT_PHOTO_DIR = Path(os.environ.get("AIRWV_REPORT_PHOTOS", "data/report_photos"))


def _save_photo(directory: Path, item_id: int, data_url: str) -> str | None:
    """Decode a base64 data-URL image and save it as <id>.<ext> under `directory`.
    Best-effort: returns the filename, or None on any decode/size failure (cap 8 MB)."""
    try:
        header, b64 = data_url.split(",", 1) if "," in data_url else ("", data_url)
        raw = base64.b64decode(b64)
        if not raw or len(raw) > 8 * 1024 * 1024:   # cap 8 MB
            return None
        ext = "png" if "image/png" in header else "jpg"
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{item_id}.{ext}"
        (directory / name).write_bytes(raw)
        return name
    except Exception:
        return None


def _save_field_photo(fid: int, data_url: str) -> str | None:
    """Save a field-reading meter photo (see _save_photo)."""
    return _save_photo(FIELD_PHOTO_DIR, fid, data_url)


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


class AlertSignupIn(BaseModel):
    email: str
    metrics: list[str] = []                 # what to watch; empty -> ["pm2_5"] (back-compat)
    sensor_id: str | None = None           # None / "" = any WV sensor (legacy single-sensor path)
    level: str = "unhealthy"               # sensitive | unhealthy | veryunhealthy
    lat: float | None = None               # area-scoped alert: center + radius
    lon: float | None = None
    radius_mi: float | None = None
    label: str | None = None               # human area name for the confirmation copy
    website: str = ""                      # honeypot
    elapsed_ms: int | None = None          # too-fast-submit guard


# Plain-language alert levels → (PM2.5 µg/m³ trigger, label). Aligned with the
# dashboard legend bands so the map colors and the alert wording agree.
ALERT_LEVELS = {
    "sensitive":     (35.0, "Unhealthy for sensitive groups"),
    "unhealthy":     (55.0, "Unhealthy for everyone"),
    "veryunhealthy": (150.0, "Very unhealthy / hazardous"),
}

# Per-metric thresholds by severity level. PM2.5 mirrors ALERT_LEVELS (µg/m³).
# VOC is the community relative gas index; ozone is EPA ppb (8-hr AQI-ish bands).
# "water", "spills" and "violations" are record alerts (alerts.RECORD_RULES): they fire
# when a record is new for the place, whatever the level, so their thresholds are 0.
METRIC_THRESHOLDS = {
    "pm2_5": {"sensitive": 35.0, "unhealthy": 55.0, "veryunhealthy": 150.0},
    "voc":   {"sensitive": 250.0, "unhealthy": 500.0, "veryunhealthy": 1000.0},
    "ozone": {"sensitive": 70.0, "unhealthy": 85.0, "veryunhealthy": 105.0},
    "water":  {"sensitive": 0.0, "unhealthy": 0.0, "veryunhealthy": 0.0},
    "spills": {"sensitive": 0.0, "unhealthy": 0.0, "veryunhealthy": 0.0},
    "violations": {"sensitive": 0.0, "unhealthy": 0.0, "veryunhealthy": 0.0},
}
ADMIN_HISTORY_CAP = 50   # records/sends listed per subscription in the admin view
METRIC_LABELS = {
    "pm2_5": "PM2.5 (fine particles)", "voc": "VOC (community gas index)",
    "ozone": "Ozone (EPA)", "water": "Drinking-water violations near me",
    "spills": "Reported spills near me", "violations": "Facility violations near me",
}

# Site kinds with measurements: /site/<kind>/<ref> charts the last N days up to the site's
# latest reading (monitors' AirData history is daily, so a month; sample sites are visited
# weeks apart, so a year); label and data source per kind, in docs/GLOSSARY.md's words.
SERIES_KINDS = {"sensor": 7, "reference_monitor": 30, "gauge": 7, "sample_site": 365}
SERIES_KIND_LABELS = {
    "sensor": ("Sensor", "a community air device; provisional readings, corrected where we can",
               "PurpleAir"),
    "reference_monitor": ("Monitor", "an EPA / WV DEP reference air monitor",
                          "EPA AirData / AQS"),
    "gauge": ("Gauge", "a USGS stream gauge, near real time", "USGS Water Services"),
    "sample_site": ("Sample site", "water samples taken and lab-tested at intervals, not live",
                    "Water Quality Portal (WQP)"),
}

# /site/permit/<ref>: the three permit layers -> (what it is, data source, raw API); the stage
# words match the map legends (app.js DEP_LABEL / MINE_LABEL)
PERMIT_LAYERS = {
    "coal_npdes": ("Coal-mine water discharge permit (NPDES)", "WV DEP Coal NPDES", "/api/coal-npdes"),
    "dep_mining": ("Mining permit", "WV DEP Division of Mining & Reclamation", "/api/dep-mining"),
    "dep_permits": ("Oil & gas well permit", "WV DEP Office of Oil & Gas", "/api/dep-permits"),
}
PERMIT_STAGE_LABELS = {
    "requested": "Requested (pending)", "approved": "Approved (permit issued)",
    "construction": "Under construction", "new": "New / not yet started",
    "active": "Active / renewed", "inactive": "Inactive (idle)",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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

# WV + bordering states (OH/PA/MD/VA/KY) — a generous box to drop bad-coordinate monitors
# (a miscoded AirNow site was plotting near Russia). Everything real sits well inside this.
def _IN_WV_REGION(lat: float, lon: float) -> bool:
    return 36.0 <= lat <= 43.0 and -84.5 <= lon <= -74.5

# Fields community sensors measure — the ones that make sense to roll up by area
# (ozone/aqi are reference-only or derived, so they're excluded from area rollups).
AREA_FIELDS = {"pm2_5", "pm1_0", "pm10", "voc", "temperature", "humidity"}
AREA_PM_MAX = 1000.0    # µg/m³; PM readings over this stay out of the per-area rollups


def _parse_date(value: str | None) -> date | None:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _hav_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dp / 2) ** 2 + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2)) * math.sin(dl / 2) ** 2)
    return 2 * 3959 * math.asin(math.sqrt(h))


def _data_json(name: str) -> dict:
    try:
        import json

        return json.loads((Path(__file__).parent.parent / "data" / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def _listing_names() -> dict[str, str]:
    """Map sensor id -> name from the resolved public listing, best-effort."""
    try:
        import json

        cache = os.environ.get("AIRWV_INDEX_CACHE", "").strip() or "data/sensor_index_map.json"
        listing = Path(cache).with_name("wv_public_sensors.json")
        return {str(r["sensor_index"]): r["name"] for r in json.loads(listing.read_text(encoding="utf-8"))
                if r.get("sensor_index") is not None and r.get("name")}
    except Exception:
        return {}


def _index_to_name() -> dict[str, str]:
    """Map stored sensor ids (PurpleAir indices) to friendly names, best-effort.

    Registry names first. The private registry is absent from worktrees and fresh clones,
    so the public listing it was matched against (same names) covers those.
    """
    try:
        from airwv.registry import load_wv_sensors
        from airwv.resolve import load_index_map

        cache = os.environ.get("AIRWV_INDEX_CACHE", "").strip() or "data/sensor_index_map.json"
        index_map = load_index_map(Path(cache))
        name_by_device = {s.device_id: s.name for s in load_wv_sensors()}
        listed = _listing_names()
        return {str(idx): name_by_device.get(dev) or listed.get(str(idx), str(idx))
                for dev, idx in index_map.items()}
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
    # gzip JSON/HTML/JS/CSS: the big layers (/api/abandoned-wells, /api/water/sites) are
    # mostly repeated keys and shrink ~8-10×
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    # added last so it runs first: a 429 costs no route work (limits are requests/minute per client)
    app.add_middleware(PublicDataMiddleware, limits={
        "api": int(os.environ.get("AIRWV_RATE_LIMIT_API", "").strip() or "300"),
        "dumps": int(os.environ.get("AIRWV_RATE_LIMIT_DUMPS", "").strip() or "30")})
    names = _index_to_name()
    coords = _index_to_coords()
    regions = _index_to_region()
    airnow_names = _airnow_meta()
    # community sensors join the read model's `site` view (resolved listing, WV regions only;
    # `region` is the /api/query area; a sensor without listed coords still counts in its area)
    store.read_model.set_layer("community_sensors", [
        {"sid": sid, "lat": coords.get(sid, (None, None))[0], "lon": coords.get(sid, (None, None))[1],
         "name": names.get(sid, sid), "region": region or "Other"}
        for sid, region in regions.items()],
        source="PurpleAir public listing (resolved)")
    facility_triggers = load_facility_triggers(Path(__file__).parent.parent / "data" / "sources.json")
    notifier = chat_notifier_from_env()   # Slack/Discord webhooks (env); disabled if unset

    _sensors_cache: dict = {}

    @app.get("/api/sensors")
    def sensors(response: Response):
        # Every page fetches this (~0.8s uncached on prod); collectors run hourly, so a
        # 60s cache costs nothing in freshness.
        import time as _t

        response.headers.update(_SHORT_CACHE)
        hit = _sensors_cache.get("v")
        if hit and _t.time() - hit[0] < 60:
            return hit[1]
        out = _sensors_uncached()
        _sensors_cache["v"] = (_t.time(), out)
        return out

    def _sensors_uncached() -> list[dict]:
        # SQL aggregation instead of loading every row (was O(all rows) per sensor).
        coverage = store.sensor_coverage()
        latest = store.latest_value_per_sensor("pm2_5")
        latest_o3 = store.latest_value_per_sensor("ozone")   # ppb — AirNow reference monitors
        # AirNow is the LIVE reference layer; AirData daily is the finalized-history archive.
        ref_ids = set(store.sensor_ids_by_source("airnow"))
        ref_coords = store.coords_from_readings("airnow")
        archive_ids = set(store.sensor_ids_by_source("epa_airdata"))   # AirData = history layer
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
            # Drop bad-coordinate monitors — a miscoded AirNow site was plotting off in
            # Russia. Keep only WV + bordering states (generous box); missing coords are fine.
            if lat is not None and lon is not None and not _IN_WV_REGION(lat, lon):
                continue
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

    _wellsvoc_cache: dict = {}

    @app.get("/api/wells-near-sensors")
    def wells_near_sensors():
        """For each community sensor: its latest VOC and how many abandoned/orphan wells
        sit nearby — the "do VOC sensors near abandoned wells read higher?" context.
        Aggregates 15k wells × ~50 sensors, so cached briefly."""
        import math
        import time as _t

        hit = _wellsvoc_cache.get("v")
        if hit and _t.time() - hit[0] < 1800:
            return hit[1]

        def _mi(la1, lo1, la2, lo2):
            dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
            h = (math.sin(dp / 2) ** 2 + math.cos(math.radians(la1))
                 * math.cos(math.radians(la2)) * math.sin(dl / 2) ** 2)
            return 2 * 3959 * math.asin(math.sqrt(h))

        try:
            import json

            wpath = Path(__file__).parent.parent / "data" / "abandoned_wells.json"
            wells = json.loads(wpath.read_text(encoding="utf-8")).get("wells", [])
        except Exception:
            wells = []
        latest_voc = store.latest_value_per_sensor("voc")
        rows = []
        for sid, region in regions.items():
            lat, lon = coords.get(sid, (None, None))
            if lat is None or lon is None:
                continue
            w2 = w5 = o2 = 0
            for w in wells:
                d = _mi(lat, lon, w["lat"], w["lon"])
                if d <= 1.24:                       # ~2 km
                    w2 += 1
                    o2 += 1 if w.get("orphan") else 0
                if d <= 3.11:                       # ~5 km
                    w5 += 1
            rows.append({"sensor_id": sid, "name": names.get(sid, sid), "region": region,
                         "voc": latest_voc.get(sid), "wells_2km": w2, "orphans_2km": o2,
                         "wells_5km": w5, "lat": lat, "lon": lon})
        vocs = [r["voc"] for r in rows if r["voc"] is not None]
        median_voc = round(statistics.median(vocs), 1) if vocs else None
        for r in rows:
            r["voc_elevated"] = r["voc"] is not None and median_voc is not None and r["voc"] > median_voc
        rows.sort(key=lambda r: (-r["wells_2km"], -(r["voc"] or 0)))
        payload = {"sensors": rows, "median_voc": median_voc,
                   "well_total": len(wells)}
        _wellsvoc_cache["v"] = (_t.time(), payload)
        return payload

    @app.get("/api/near")
    def near(lat: float, lon: float, km: float = 8.0):
        """Everything potentially health-relevant near a point — the 'what's around me
        and could it explain how I feel?' aggregation across all our layers. Each hazard
        carries a broad category (gas / air / water / chemical / sensor) the UI matches
        to symptoms. Not medical advice. Reads the unified views (site / measurement /
        record, storage/readmodel.py); tests/snapshots/api_near.json pins the output.
        ``link`` is the data source's own record; ``page`` is the site's /site/<kind>/<ref>."""
        km = min(max(km, 1.0), 25.0)
        radius_mi = km * 0.621371
        rm = store.read_model

        def flags(conn, sites):
            recs = rm.records_for(conn, [s["site_id"] for s in sites])
            return [(s, {r["kind"] for r in recs.get(s["site_id"], [])}) for s in sites]

        with rm.connect() as conn:
            def near_pts(layer, cap, make):
                sites = rm.sites_near(conn, lat, lon, radius_mi, layer=layer, limit=cap)
                return [{**make(s, s["attrs"], f, round(s["d"], 1)), "page": site_page_url(s["kind"], s["ref"])}
                        for s, f in flags(conn, sites)]

            haz: list[dict] = []

            # abandoned / orphan gas wells → gas (H2S / natural gas)
            haz += near_pts("abandoned_wells", 12, lambda s, w, f, d: {
                "category": "gas", "icon": "🛢️",
                "label": ("Orphan" if "orphan" in f else "Abandoned") + " gas well",
                "detail": (f"{w['nearest_building_m']} m from a building" if w.get("near_homes")
                           else (s["county"] and f"{s['county']} County")),
                "flag": "orphan" if "orphan" in f else None, "mi": d, "lat": s["lat"], "lon": s["lon"],
                "link": s["url"], "report": True})

            # EPA ECHO major facilities → air or water by program; violations flagged
            haz += near_pts("echo_facilities", 8, lambda s, a, f, d: {
                "category": s["medium"], "icon": "⚖️", "label": s["name"],
                "detail": (a.get("compliance_status") or "").strip() or "regulated facility",
                "flag": "violation" if "violation" in f else None,
                "mi": d, "lat": s["lat"], "lon": s["lon"], "link": s["url"], "report": True})

            # curated TRI / documented pollution sources → air/chemical
            haz += near_pts("sources", 8, lambda s, a, f, d: {
                "category": "air", "icon": "🏭", "label": s["name"],
                "detail": a.get("type") or a.get("operator"), "flag": None, "mi": d,
                "lat": s["lat"], "lon": s["lon"], "link": s["url"], "report": True})

            # coal-mine water discharges → water
            haz += near_pts("coal_npdes", 6, lambda s, p, f, d: {
                "category": "water", "icon": "⛏️", "label": f"Coal discharge — {p.get('operator')}",
                "detail": (f"impaired for {', '.join(p.get('impairment_causes', [])[:2])}"
                           if "impaired_303d" in f else f"{p.get('outlets')} outlets"),
                "flag": "impaired" if "impaired_303d" in f else None, "mi": d,
                "lat": s["lat"], "lon": s["lon"], "link": s["url"], "report": False})

            # active mining permits → air (dust) / land
            haz += near_pts("dep_mining", 6, lambda s, m, f, d: {
                "category": "air", "icon": "⛏️", "label": f"{m.get('type') or 'Mining'} — {m.get('operator')}",
                "detail": (f"{m.get('acres_disturbed')} acres disturbed" if m.get("acres_disturbed")
                           else m.get("permit_status")),
                "flag": None, "mi": d, "lat": s["lat"], "lon": s["lon"], "link": None, "report": True})

            # NRC reported spills → chemical (and water when they reached water)
            haz += near_pts("nrc_spills", 6, lambda s, a, f, d: {
                "category": "water" if s["medium"] == "water" else "chemical", "icon": "🛢️",
                "label": "Reported spill — " + (", ".join(m["name"] for m in a.get("materials", [])[:2])
                                                or "release"),
                "detail": f"{a.get('date') or ''}{' · reached water' if a.get('reached_water') else ''}".strip(" ·"),
                "flag": "spill", "mi": d, "lat": s["lat"], "lon": s["lon"],
                "link": s["url"], "report": False})

            # community sensors → sensor (VOC / PM2.5 right now)
            sensors = rm.sites_near(conn, lat, lon, radius_mi, layer="community_sensors", limit=4)
            refs = [s["ref"] for s in sensors]
            latest_pm = rm.latest(conn, "air", refs, "pm2_5")
            latest_voc = rm.latest(conn, "air", refs, "voc")
            for s in sensors:
                pm = latest_pm.get(s["ref"], {}).get("pm2_5", {}).get("value")
                voc = latest_voc.get(s["ref"], {}).get("voc", {}).get("value")
                haz.append({
                    "category": "sensor", "icon": "📡", "label": s["name"] + " sensor",
                    "detail": " · ".join(x for x in [
                        (pm is not None and f"PM2.5 {round(pm, 1)}"),
                        (voc is not None and f"VOC {round(voc, 0):g}")] if x) or "community sensor",
                    "flag": None, "mi": round(s["d"], 1), "lat": s["lat"], "lon": s["lon"],
                    "link": "/air", "report": False, "page": site_page_url(s["kind"], s["ref"])})

            # measured water sample sites → water
            wsites = rm.sites_near(conn, lat, lon, radius_mi, layer="water_readings", limit=10_000, bbox_km=km)
            wsites = sorted(wsites, key=lambda s: (round(s["d"], 1), s["ref"]))[:4]   # nearest by 0.1 mi
            wlatest = rm.latest(conn, "water", [s["ref"] for s in wsites])
            for s in wsites:
                latest = wlatest.get(s["ref"], {})
                keys = [k for k in ("selenium", "iron", "manganese", "sulfate", "conductance", "ph", "ecoli")
                        if k in latest]
                haz.append({"category": "water", "icon": "💧", "label": s["name"] + " (water sample)",
                            "detail": ", ".join(f"{k} {latest[k]['value']}" for k in keys[:3]) or "sample site",
                            "flag": None, "mi": round(s["d"], 1), "lat": s["lat"], "lon": s["lon"],
                            "link": "/water", "report": False, "page": site_page_url(s["kind"], s["ref"])})

            # drinking-water systems with a health-based violation in the nearest county → water
            from airwv.wvgeo import WV_COUNTY_CENTROID
            near_county = min(WV_COUNTY_CENTROID,
                              key=lambda c: _hav_mi(lat, lon, *WV_COUNTY_CENTROID[c]), default=None)
            if near_county:
                systems = rm.sites(conn, layer="sdwa_systems", where="county = :c", params={"c": near_county})
                for sy, f in flags(conn, systems):
                    if "health_violation" in f:
                        haz.append({"category": "water", "icon": "🚰",
                                    "label": f"{sy['name']} (water system)",
                                    "detail": f"health-based SDWA violation · serves {sy['attrs'].get('population')}",
                                    "flag": "violation", "mi": None, "lat": sy["lat"], "lon": sy["lon"],
                                    "link": sy["url"], "report": False,
                                    "page": site_page_url(sy["kind"], sy["ref"])})

        cats: dict = defaultdict(int)
        for h in haz:
            cats[h["category"]] += 1
        return {"lat": lat, "lon": lon, "km": km, "county": near_county,
                "hazards": haz, "counts": dict(cats)}

    @app.get("/api/query")
    def query(lat: float | None = None, lon: float | None = None, km: float = 8.0,
              kind: str | None = None, medium: str | None = None, area: str | None = None,
              county: str | None = None, param: str | None = None, since: str | None = None,
              from_: str | None = Query(None, alias="from"), to: str | None = None,
              over: float | None = None, site_id: str | None = None, limit: int | None = None,
              format: str = "json", show: str = "sites"):
        """One read path over the unified views: sites near a place (``kind`` is a comma
        list: well, facility, permit, spill_report, water_system, reference_monitor,
        sensor, gauge, sample_site, data_center; ``medium`` air / water / land; ``area`` a
        county or a WV region, with or without a point; ``county`` a comma list of WV
        counties, matching the county a site's own data lists, so sites with none listed,
        like sensors and gauges, drop out), each with its records, its latest
        ``param`` value when it has measurements (``param`` may be a comma list: the first
        one the site has wins; ``from`` / ``to``, YYYY-MM-DD inclusive, bound which
        measurements count), and the provenance of every layer it came from. ``since``
        (YYYY-MM-DD) adds "what changed": a ``changes`` list of records dated on/after it
        and each measured site's ``peak`` of the first ``param`` since then (up to ``to``).
        ``over`` (needs ``param``) keeps only sites whose latest value is above it, before
        ``limit`` applies; in series mode, only the rows above it.
        ``km`` is capped at 25 mi. ``format=csv`` returns the same answer as a CSV file: one
        row per site, or with ``show=records`` one row per record (only those dated on/after
        ``since`` when it's given). Series mode: ``site_id`` (one site, ``kind:ref``) + one
        ``param`` + optional ``from`` / ``to`` returns that site's measurement rows, oldest
        first (the newest ``limit``, default 5000, at most 20000), and a ``csv`` link to the
        same rows; the place filters don't apply. Powers /explore. See docs/READ-MODEL.md."""
        km = min(max(km, 1.0), 40.25)
        kinds = [k.strip() for k in kind.split(",") if k.strip()] if kind else None
        params = [p.strip() for p in param.split(",") if p.strip()] if param else []
        area = (area or "").strip() or None
        point = lat is not None and lon is not None
        site_id = (site_id or "").strip() or None
        counties = []
        for c in (county or "").split(","):
            if c.strip():
                if not county_names(c):
                    raise HTTPException(status_code=400, detail=f"not a WV county: {c.strip()}")
                counties += county_names(c)
        if not point and not area and not counties and not site_id:
            raise HTTPException(status_code=400, detail="give lat and lon, an area, a county, or a site_id")
        if over is not None and (not params or not math.isfinite(over)):
            raise HTTPException(status_code=400, detail="over needs a param and a finite number")
        dates = {}
        for name, val in (("since", since), ("from", from_), ("to", to)):
            if val is not None:
                try:
                    dates[name] = date.fromisoformat(val[:10]).isoformat()
                except ValueError:
                    raise HTTPException(status_code=400, detail=f"{name} must be YYYY-MM-DD")
        since, from_, to = dates.get("since"), dates.get("from"), dates.get("to")
        if format not in ("json", "csv") or show not in ("sites", "records"):
            raise HTTPException(status_code=400, detail="format is json or csv; show is sites or records")
        if site_id:
            return _query_series(site_id, params, from_, to, limit, format, over)
        limit = min(max(100 if limit is None else limit, 1), 500)
        rm = store.read_model
        with rm.connect() as conn:
            # `over` is decided on values, so it reads every measured site and limits afterwards
            measured = ["sensor", "reference_monitor", "gauge", "sample_site"]
            sites = rm.sites_near(conn, lat if point else None, lon if point else None, km * 0.621371,
                                  kinds=(kinds or measured) if over is not None else kinds,
                                  medium=medium, area=area, county=counties,
                                  limit=-1 if over is not None else limit)

            def refs(med):
                return [s["ref"] for s in sites if s["medium"] == med and s["kind"] in measured]

            def newest(s):
                by_param = latest.get(s["medium"], {})
                return next(({**by_param[p][s["ref"]][p], "parameter": p} for p in params
                             if s["ref"] in by_param.get(p, {})), None)

            def is_over(s):
                value = (newest(s) or {}).get("value")
                return value is not None and value > over

            latest: dict = {}
            peaks: dict = {}
            for med in ("air", "water") if params else ():
                latest[med] = {p: rm.latest(conn, med, refs(med), p, since=from_, until=to) for p in params}
            if over is not None:
                sites = [s for s in sites if is_over(s)][:limit]
            for med in ("air", "water") if params and since else ():
                peaks[med] = rm.peak(conn, med, refs(med), params[0], max(since, from_ or since), until=to)
            recs = rm.records_for(conn, [s["site_id"] for s in sites])
            prov = rm.provenance(conn, sorted({s["layer"] for s in sites}))
        out, changes = [], []
        for s in sites:
            row = {k: s[k] for k in ("site_id", "kind", "medium", "layer", "ref", "name",
                                     "lat", "lon", "county", "url")}
            row["mi"] = round(s["d"], 2) if s["d"] is not None else None
            row["records"] = recs.get(s["site_id"], [])
            if params:
                row["latest"] = newest(s)
            if since:
                pk = peaks.get(s["medium"], {}).get(s["ref"])
                if params:
                    row["peak"] = pk and {**pk, "parameter": params[0]}
                changes += [{"site_id": s["site_id"], "site_kind": s["kind"], "name": s["name"],
                             "medium": s["medium"], "mi": row["mi"], **r}
                            for r in row["records"] if (r["effective"] or "")[:10] >= since]
            out.append(row)
        body = {"place": {"lat": lat, "lon": lon, "km": km} if point else None,
                "filters": {"kind": kinds, "medium": medium, "area": area, "county": counties or None,
                            "param": param, "over": over, "since": since, "from": from_, "to": to,
                            "limit": limit},
                "count": len(out), "sites": out, "provenance": prov}
        if since:
            body["changes"] = sorted(changes, key=lambda c: c["effective"], reverse=True)
        if format == "csv":
            if show == "records":
                text = query_records_to_csv(body["changes"] if since else [
                    {"site_id": s["site_id"], "site_kind": s["kind"], "name": s["name"],
                     "medium": s["medium"], "mi": s["mi"], **r} for s in out for r in s["records"]])
            else:
                text = query_sites_to_csv(out)
            return Response(content=text, media_type="text/csv", headers={
                "Content-Disposition": f'attachment; filename="airwv_query_{show}.csv"'})
        return body

    def _query_series(site_id, params, from_, to, limit, format, over):
        """/api/query series mode: one site's measurement rows of one parameter."""
        if len(params) != 1:
            raise HTTPException(status_code=400, detail="series mode takes exactly one param")
        param = params[0]
        limit = min(max(5000 if limit is None else limit, 1), 20000)
        rm = store.read_model
        with rm.connect() as conn:
            site = rm.site(conn, site_id)
            if site is None:
                raise HTTPException(status_code=404, detail="no site with that site_id")
            rows, truncated = rm.series(conn, site["medium"], measured_refs(site), param,
                                        since=from_, until=to, limit=limit, over=over)
            prov = rm.provenance(conn, [site["layer"]])
        if format == "csv":
            return Response(content=query_series_to_csv(site, param, rows), media_type="text/csv",
                            headers={"Content-Disposition": 'attachment; filename="airwv_query_series.csv"'})
        q = {"site_id": site_id, "param": param, "from": from_, "to": to, "over": over}
        csv_url = "/api/query?" + urlencode({k: v for k, v in q.items() if v is not None}
                                            | {"limit": limit, "format": "csv"})
        return {"mode": "series",
                "site": {k: site[k] for k in ("site_id", "kind", "medium", "layer", "ref", "name",
                                              "lat", "lon", "county", "url")},
                "filters": {**q, "limit": limit},
                "count": len(rows), "truncated": truncated, "rows": rows, "csv": csv_url,
                "provenance": prov}

    @app.get("/api/data-catalog")
    def data_catalog():
        """Public provenance catalog — every data layer, its source, freshness, and
        record count. Freshness/counts are read live from the data files; see
        docs/DATA-SOURCES.md for the full field guide."""
        def meta(fname, key):
            d = _data_json(fname)
            arr = d.get(key, [])
            return {"fetched_at": d.get("fetched_at"),
                    "count": d.get("count") or (len(arr) if isinstance(arr, list) else None)}

        cov = store.coverage_overall()
        cov_last = cov.get("last_ts")
        cov_last = cov_last.isoformat()[:10] if cov_last else None
        # (name, category, org, access, keyless, record, url, api, freshness/count)
        catalog = [
            {"name": "PurpleAir community sensors", "category": "Air", "org": "PurpleAir",
             "access": "REST API", "keyless": False, "record": "community low-cost sensors",
             "url": "https://www2.purpleair.com/", "api": "/api/sensors",
             "fetched_at": cov_last, "count": None, "freshness": "on a collection timer"},
            {"name": "EPA AirNow (live reference)", "category": "Air", "org": "US EPA",
             "access": "keyless bulk hourly file", "keyless": True, "record": "regulatory monitors (hourly)",
             "url": "https://www.airnow.gov/", "api": "/api/sensors", "freshness": "hourly, keyless"},
            {"name": "EPA AirData / AQS (history)", "category": "Air", "org": "US EPA",
             "access": "keyless annual bulk files", "keyless": True, "record": "QA'd daily history since 2007",
             "url": "https://aqs.epa.gov/aqsweb/airdata/", "api": "/api/validate", "freshness": "annual"},
            {"name": "USGS NWIS gauges", "category": "Water", "org": "USGS",
             "access": "REST JSON", "keyless": True, "record": "real-time stream gauges",
             "url": "https://waterservices.usgs.gov/", "api": "/api/water/sites", "freshness": "real-time"},
            {"name": "EPA Water Quality Portal", "category": "Water", "org": "EPA / USGS",
             "access": "REST CSV", "keyless": True, "record": "lab & grab samples (metals, E. coli, …)",
             "url": "https://www.waterqualitydata.us/", "api": "/api/water/near", "freshness": "periodic"},
            {"name": "EPA ECHO — facility compliance", "category": "Facilities", "org": "US EPA",
             "access": "REST (qid paging)", "keyless": True, "record": "major regulated facilities + violations",
             "url": "https://echo.epa.gov/", "api": "/api/facilities", **meta("echo_facilities.json", "facilities")},
            {"name": "EPA SDWIS — drinking water", "category": "Water", "org": "US EPA",
             "access": "REST (ECHO sdw)", "keyless": True, "record": "public water systems + SDWA violations",
             "url": "https://echo.epa.gov/", "api": "/api/sdwa", **meta("sdwa_systems.json", "systems")},
            {"name": "EPA TRI (Envirofacts)", "category": "Facilities", "org": "US EPA",
             "access": "REST", "keyless": True, "record": "toxic-release facilities",
             "url": "https://www.epa.gov/toxics-release-inventory-tri-program", "api": "/api/sources",
             **meta("sources.json", "sources")},
            {"name": "WV DEP — oil & gas permits", "category": "Permits", "org": "WV DEP",
             "access": "ArcGIS REST", "keyless": True, "record": "O&G permit pipeline",
             "url": "https://tagis.dep.wv.gov/", "api": "/api/dep-permits", **meta("dep_permits.json", "permits")},
            {"name": "WV DEP — mining permits", "category": "Permits", "org": "WV DEP",
             "access": "ArcGIS REST", "keyless": True, "record": "active/upcoming mining + acreage",
             "url": "https://tagis.dep.wv.gov/", "api": "/api/dep-mining", **meta("dep_mining.json", "mines")},
            {"name": "WV DEP — coal NPDES + 303(d)", "category": "Water", "org": "WV DEP",
             "access": "ArcGIS REST (spatial join)", "keyless": True, "record": "coal discharges on impaired streams",
             "url": "https://tagis.dep.wv.gov/", "api": "/api/coal-npdes", **meta("coal_npdes.json", "permits")},
            {"name": "WV DEP — abandoned wells", "category": "Wells", "org": "WV DEP",
             "access": "ArcGIS REST", "keyless": True, "record": "abandoned & orphan gas wells",
             "url": "https://tagis.dep.wv.gov/", "api": "/api/abandoned-wells", **meta("abandoned_wells.json", "wells")},
            {"name": "National Response Center", "category": "Events", "org": "US Coast Guard",
             "access": "annual .xlsx", "keyless": True, "record": "reported oil/chemical spills",
             "url": "https://nrc.uscg.mil/", "api": "/api/nrc-spills", **meta("nrc_spills.json", "spills")},
            {"name": "Data centers & crypto mining (desk research)", "category": "Facilities",
             "org": "Empower WV (from DEP, PSC, company and news records)", "access": "hand-compiled",
             "keyless": True, "record": "data centers: power, water, noise, use, what's not disclosed",
             "url": "/site/data_center/monarch-compute-campus", "api": "/api/data-centers",
             **meta("data_centers.json", "sites")},
            {"name": "Microsoft US Building Footprints", "category": "Geospatial", "org": "Microsoft",
             "access": "open GeoJSON", "keyless": True, "record": "buildings (near-homes well risk)",
             "url": "https://github.com/microsoft/USBuildingFootprints", "api": "/api/abandoned-wells",
             "freshness": "static (2021)"},
            {"name": "Iowa State Mesonet (ASOS)", "category": "Weather", "org": "Iowa State",
             "access": "CSV", "keyless": True, "record": "wind roses for dispersion",
             "url": "https://mesonet.agron.iastate.edu/", "api": "/api/wind-roses", "freshness": "periodic"},
        ]
        keyless = sum(1 for s in catalog if s.get("keyless"))
        return {"sources": catalog, "total": len(catalog), "keyless": keyless,
                "categories": sorted({s["category"] for s in catalog})}

    @app.get("/api/climate/trend")
    def climate_trend(field: str = "ozone"):
        """Annual trend for a field from our own stored history — used by the Learn
        Climate tab so the climate story is grounded in WV data, not just external framing.
        Ozone/PM2.5 come from EPA reference monitors; temperature from community sensors."""
        if field not in FIELDS:
            raise HTTPException(status_code=400, detail=f"unknown field {field!r}")
        # reference-grade fields draw from the EPA monitors; weather from community sensors
        sources = (["airnow", "epa_airdata"] if field in ("ozone", "pm2_5", "pm10")
                   else ["purpleair"])
        units = {"ozone": "ppb", "pm2_5": "µg/m³", "pm10": "µg/m³",
                 "temperature": "°F", "humidity": "%"}
        years = store.annual_field_stats(field, sources=sources)
        return {"field": field, "unit": units.get(field, ""), "sources": sources, "years": years}

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
        rows = [{"parameter": cr.parameter, "value": cr.value, "unit": cr.unit,
                 "verified": cr.verified}
                for cr in store.community_readings_for_report(r.id)]
        return {
            "id": r.id, "created_at": r.created_at.isoformat() if r.created_at else None,
            "observed_at": r.observed_at.isoformat() if r.observed_at else None,
            "domain": r.domain, "category": r.category, "description": r.description,
            "lat": lat, "lon": lon, "area_label": r.area_label, "stage": r.stage,
            "verified": r.stage == "confirmed", "verified_by": r.verified_by,
            "org": r.suspected_org if r.org_public else None,
            "readings": rows,
            "photo": f"/api/reports/{r.id}/photo" if (r.photo_path and r.photo_ok) else None,
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
            lat=body.lat, lon=body.lon, area_label=(body.area_label or None)[:120] if body.area_label else None,
            observed_at=_parse_dt(body.observed_at),
            suspected_org=(body.suspected_org or None), contact_name=(body.contact_name or None),
            contact_email=(body.contact_email or None), contact_phone=(body.contact_phone or None),
            ip_hash=iph, stage=stage, screen_reason=reason)
        # optional structured measurements — stored as community readings, unverified
        for row in (body.readings or [])[:20]:
            if not (row.parameter or "").strip():
                continue
            store.add_community_reading(
                report_id=rid, domain=body.domain, parameter=row.parameter[:64],
                value=row.value, unit=(row.unit or "")[:32], method=(row.method or None),
                lat=body.lat, lon=body.lon, taken_at=_parse_dt(body.observed_at))
        # optional photo — held (photo_ok=False) until a maintainer approves the image
        if body.photo:
            name = _save_photo(REPORT_PHOTO_DIR, rid, body.photo)
            if name:
                store.set_report(rid, photo_path=name)
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

    # -- alert sign-up (public, double opt-in) -----------------------------

    def _base_url(request: Request) -> str:
        # Behind the proxy request.base_url can be http://; prefer an explicit
        # public base when set so confirm/unsubscribe links use https + host.
        return os.environ.get("AIRWV_BASE_URL", "").strip().rstrip("/") \
            or str(request.base_url).rstrip("/")

    _signup_hits: dict[str, list[float]] = {}

    def _signup_rate_ok(iph: str) -> bool:
        import time as _t
        now = _t.time()
        hits = [t for t in _signup_hits.get(iph, []) if now - t < 3600]
        hits.append(now)
        _signup_hits[iph] = hits
        return len(hits) <= 5

    @app.post("/api/alerts/subscribe")
    def alerts_subscribe(body: AlertSignupIn, request: Request, background: BackgroundTasks):
        if body.website.strip():
            raise HTTPException(status_code=400, detail="rejected")            # honeypot
        if body.elapsed_ms is not None and body.elapsed_ms < 1200:
            raise HTTPException(status_code=400, detail="submitted too fast")  # bot
        email = (body.email or "").strip().lower()
        if not _EMAIL_RE.match(email) or len(email) > 254:
            raise HTTPException(status_code=400, detail="please enter a valid email address")
        if body.level not in ALERT_LEVELS:
            raise HTTPException(status_code=400, detail="unknown alert level")
        if not _signup_rate_ok(ip_hash(client_ip(request))):
            raise HTTPException(status_code=429, detail="too many sign-ups — try again later")

        level_label = ALERT_LEVELS[body.level][1]
        # metrics to watch (multi-select); default PM2.5 for legacy single-level clients
        metrics = [m for m in (body.metrics or []) if m in METRIC_THRESHOLDS] or ["pm2_5"]
        sensor_id = (body.sensor_id or "").strip() or None

        # location: an explicit point + radius scopes the alert to an area; else any WV sensor
        center_lat = center_lon = radius_km = None
        if body.lat is not None and body.lon is not None and _IN_WV_REGION(body.lat, body.lon):
            center_lat, center_lon = float(body.lat), float(body.lon)
            radius_km = max(1.0, float(body.radius_mi or 10) * 1.60934)
        if body.label and body.label.strip():
            area = body.label.strip()[:120]
        elif sensor_id:
            area = names.get(sensor_id, "your sensor")
        elif center_lat is not None:
            area = f"within {body.radius_mi or 10:g} mi of your spot"
        else:
            area = "any WV sensor"

        # One subscription per metric, all sharing a token so a single confirm/unsubscribe
        # link governs the whole group. Idempotent: re-signup updates rows in place.
        existing_any = store.find_subscription(target=email, field=metrics[0], sensor_id=sensor_id)
        token = (existing_any.token if existing_any and existing_any.token else secrets.token_urlsafe(24))
        confirmed = bool(existing_any and existing_any.confirmed_at is not None)
        for metric in metrics:
            threshold = METRIC_THRESHOLDS[metric][body.level]
            found = store.find_subscription(target=email, field=metric, sensor_id=sensor_id)
            kind = "record" if metric in RECORD_RULES else "threshold"
            fields = dict(sensor_id=sensor_id, field=metric, threshold=threshold, kind=kind,
                          center_lat=center_lat, center_lon=center_lon, radius_km=radius_km,
                          label=area, token=token)
            if kind == "record":
                fields["records_checked_at"] = None   # the place may have moved: new baseline
            if found is not None:
                store.update_subscription(found.id, **fields)
            else:
                store.add_subscription(channel="email", target=email,
                                       active=confirmed, **fields)

        watching = ", ".join(METRIC_LABELS.get(m, m) for m in metrics)
        base = _base_url(request)
        confirm_url = f"{base}/alerts/confirm?token={token}"
        unsub_url = f"{base}/alerts/unsubscribe?token={token}"
        sent = False
        if not confirmed and email_enabled():
            body_txt = (
                f"Thanks for signing up for AirWV environmental alerts.\n\n"
                f"You asked to be emailed about {watching} near {area}, at the "
                f"“{level_label}” level.\n\n"
                f"Please confirm this address to turn the alerts on:\n{confirm_url}\n\n"
                f"If you didn't request this, ignore this email — nothing happens "
                f"until you confirm.\n\nUnsubscribe any time: {unsub_url}\n"
            )
            background.add_task(send_email, email,
                                "Confirm your AirWV alerts", body_txt)
            sent = True

        if confirmed:
            msg = "You're already confirmed — we updated what you're watching and where."
        elif sent:
            msg = "Almost there — check your email for a confirmation link to turn on alerts."
        else:
            # SMTP not live yet: we captured the sign-up as a waitlist entry.
            msg = ("You're on the list. Email alert delivery is still being finished — "
                   "we'll send a confirmation link the moment it goes live.")
        return {"ok": True, "confirmed": confirmed, "email_sent": sent,
                "level": level_label, "area": area, "watching": watching, "message": msg}

    def _alert_result_page(request: Request, title: str, body_html: str):
        return TEMPLATES.TemplateResponse(
            request=request, name="alerts_result.html",
            context={"mode": "alerts", "title": title, "body_html": body_html})

    @app.get("/alerts/confirm", response_class=HTMLResponse)
    def alerts_confirm(request: Request, token: str = ""):
        sub = store.confirm_subscription(token, datetime.now(tz=timezone.utc)) if token else None
        if sub is None:
            return _alert_result_page(request, "Link not found",
                "<p>That confirmation link is invalid or expired. "
                "<a href='/alerts'>Sign up again →</a></p>")
        area = sub.label or "your area"
        return _alert_result_page(request, "You're all set ✅",
            f"<p>Alerts are on. We'll email <b>{html.escape(sub.target)}</b> when something you "
            f"watch near <b>{html.escape(area)}</b> crosses your level, or a new spill or violation "
            f"you asked about shows up in the public records — with quiet hours and rate limits "
            f"so you're never spammed.</p><p><a href='/air'>See the live map →</a></p>")

    @app.get("/alerts/unsubscribe", response_class=HTMLResponse)
    def alerts_unsubscribe(request: Request, token: str = ""):
        sub = store.deactivate_subscription(token) if token else None
        if sub is None:
            return _alert_result_page(request, "Link not found",
                "<p>We couldn't find that subscription — it may already be off.</p>")
        return _alert_result_page(request, "Unsubscribed",
            f"<p>Done — <b>{html.escape(sub.target)}</b> will no longer receive these alerts. "
            f"Changed your mind? <a href='/alerts'>Sign up again →</a></p>")

    # -- admin / moderation (token-gated via AIRWV_ADMIN_TOKEN) -------------

    def _admin_report(r) -> dict:
        return {
            "id": r.id, "created_at": r.created_at, "observed_at": r.observed_at,
            "domain": r.domain, "category": r.category, "description": r.description,
            "lat": r.lat, "lon": r.lon, "area_label": r.area_label, "stage": r.stage,
            "screen_reason": r.screen_reason, "flags_count": r.flags_count, "verified_by": r.verified_by,
            "suspected_org": r.suspected_org, "org_public": r.org_public,
            "photo_ok": r.photo_ok,
            # admin sees the image even before it's approved, so it can be vetted
            "photo": f"/api/admin/reports/{r.id}/photo" if r.photo_path else None,
            "readings": [{"parameter": cr.parameter, "value": cr.value, "unit": cr.unit,
                          "verified": cr.verified}
                         for cr in store.community_readings_for_report(r.id)],
            "contact_name": r.contact_name, "contact_email": r.contact_email, "contact_phone": r.contact_phone,
            "ip_hash": r.ip_hash, "mod_note": r.mod_note,
        }

    @app.get("/api/admin/reports")
    def admin_reports(status: str = "held", _: bool = Depends(_require_admin)):
        return {"results": [_admin_report(r) for r in store.reports_for_admin(status)]}

    @app.get("/api/admin/reports/{report_id}/photo")
    def admin_report_photo(report_id: int, _: bool = Depends(_require_admin)):
        """Serve a report's photo to a maintainer for vetting — regardless of photo_ok,
        so they can decide whether to approve it for public view."""
        r = store.get_report(report_id)
        if not r or not r.photo_path:
            raise HTTPException(status_code=404, detail="no photo")
        path = REPORT_PHOTO_DIR / r.photo_path
        if not path.exists():
            raise HTTPException(status_code=404, detail="missing file")
        return FileResponse(path)

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

    def _record_history(sub, rows) -> dict:
        """What a record subscription fired on (one entry per send, newest first) and what
        was taken as its starting point. Lists stop at ADMIN_HISTORY_CAP; the counts don't."""
        def record(key):
            site_id, kind, value = key.split("|", 2)
            return {"site_id": site_id, "kind": kind, "value": value or None}

        sends: dict = {}
        baseline = []
        for key, origin, at in rows:
            if origin == "alert":
                sends.setdefault(at, []).append(record(key))
            elif origin == "baseline":
                baseline.append((key, at))
        return {
            "checked_at": sub.records_checked_at.isoformat() if sub.records_checked_at else None,
            "sends": len(sends),
            "fired": [{"at": at.isoformat(), "records": recs}
                      for at, recs in list(sends.items())[:ADMIN_HISTORY_CAP]],
            "baseline": {"taken_at": baseline[0][1].isoformat() if baseline else None,
                         "count": len(baseline),
                         "records": [record(k) for k, _at in baseline[:ADMIN_HISTORY_CAP]]},
            # alert_seen rows stored before origin was tracked: can't say which they were
            "unlabeled": sum(1 for _k, origin, _at in rows if origin is None),
        }

    @app.get("/api/admin/subscriptions")
    def admin_subscriptions(_: bool = Depends(_require_admin)):
        """Alert sign-ups — including the pending/unconfirmed waitlist — and, for record
        subscriptions, their alert history."""
        subs = store.list_subscriptions(active_only=False)
        history = store.record_history(s.id for s in subs if is_record_sub(s))
        return {"email_delivery": email_enabled(),
                "results": [{"id": s.id, "email": s.target, "channel": s.channel,
                             "sensor_id": s.sensor_id, "area": s.label,
                             "field": s.field, "kind": s.kind, "threshold": s.threshold,
                             "radius_km": s.radius_km,
                             "center": ([s.center_lat, s.center_lon]
                                        if s.center_lat is not None else None),
                             "active": s.active,
                             "confirmed": s.confirmed_at is not None,
                             "created_at": s.created_at.isoformat() if s.created_at else None,
                             "record_history": (_record_history(s, history[s.id])
                                                if s.id in history else None)}
                            for s in subs]}

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

    @app.get("/api/admin/event-candidates")
    def admin_event_candidates(field: str = "pm2_5", days: int = 30, start: str | None = None,
                               end: str | None = None, radius_km: float = 15.0,
                               _: bool = Depends(_require_admin)):
        """Spikes cross-checked against neighbors; corroborated ones proposed as draft events."""
        _check_field(field)
        from zoneinfo import ZoneInfo

        from airwv.analysis.crosscheck import matching_event
        from airwv.ingest import run_candidates

        lo, hi = _parse_date(start), _parse_date(end)
        r = run_candidates(store=store, coords=coords, field=field, days=max(1, min(days, 120)),
                           start=datetime.combine(lo, time.min) if lo else None,
                           end=datetime.combine(hi, time.max) if hi else None, radius_km=radius_km)
        curated = store.events_for_admin()
        et = ZoneInfo("America/New_York")
        unit = " µg/m³" if field.startswith("pm") else ""

        def name(sid):
            return names.get(sid) or airnow_names.get(sid) or sid

        def local(ts):
            return ts.replace(tzinfo=timezone.utc).astimezone(et).strftime("%b %-d %-I:%M %p ET")

        out = []
        for c in r["candidates"]:
            near = matching_event(c, curated)
            seen = Counter(regions[s] for s in c.sensor_ids if regions.get(s, "Other") != "Other")
            region = seen.most_common(1)[0][0] if seen else None
            draft = {
                "title": f"Sensor-detected {field.upper().replace('_', '.')} rise — "
                         f"{region or 'nearby sensors'} ({c.start:%b %-d, %Y})",
                "medium": "air", "kind": "other", "region": region, "lat": c.lat, "lon": c.lon,
                "start_ts": c.start.isoformat() + "Z", "end_ts": c.end.isoformat() + "Z",
                "description": (
                    f"{len(c.sensor_ids)} sensors within {radius_km:g} km rose above their usual "
                    f"level for the hour of day between {local(c.start)} and {local(c.end)}; highest "
                    f"{c.peak_value:g}{unit} at {name(c.peak_sensor)}. Proposed automatically by the "
                    "neighbor cross-check; the cause is not yet known."),
                "captured": True, "sensor_ids": c.sensor_ids, "status": "draft",
            }
            out.append({
                "start_ts": draft["start_ts"], "end_ts": draft["end_ts"], "field": c.field,
                "sensor_ids": c.sensor_ids, "sensor_names": [name(s) for s in c.sensor_ids],
                "peak": {"sensor_id": c.peak_sensor, "name": name(c.peak_sensor), "value": c.peak_value,
                         "ts": c.peak_ts.isoformat() + "Z", "score": c.peak_score},
                "lat": c.lat, "lon": c.lon, "region": region,
                "existing_event": {"id": near.id, "title": near.title} if near else None,
                "draft": draft,
            })
        single = [{"sensor_id": c.episode.sensor_id, "name": name(c.episode.sensor_id),
                   "ts": c.episode.peak.ts.isoformat() + "Z", "value": c.episode.peak.value,
                   "score": c.episode.peak.score, "neighbors": c.neighbors, "neighbor_peak_z": c.peak_z}
                  for c in sorted((c for c in r["checks"] if c.status == "single_sensor"),
                                  key=lambda c: -c.episode.peak.score)]
        counts = {s: sum(c.status == s for c in r["checks"])
                  for s in ("corroborated", "single_sensor", "no_neighbors")}
        return {"field": field, "radius_km": radius_km,
                "since": r["since"].isoformat() + "Z" if r["since"] else None,
                "until": r["until"].isoformat() + "Z" if r["until"] else None,
                "counts": counts, "candidates": out, "single_sensor": single[:50]}

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

    # -- per-area rollups (community sensors grouped by WV region) ----------

    _areas_cache: dict = {}

    def _region_groups(conn, region: str | None = None) -> dict[str, list[str]]:
        """Community-sensor refs per WV region, from the read model's `site` view (the
        same ``area`` filter /api/query takes), in listing order."""
        groups: dict[str, list[str]] = defaultdict(list)
        for s in store.read_model.sites_near(conn, None, None, 0, layer="community_sensors",
                                             area=region, limit=-1):
            groups[s["attrs"]["region"]].append(s["ref"])
        return groups

    def _area_daily(conn, sids, field: str, since):
        """Pooled daily series across a region's sensors → sorted [(date, median, n)].
        Uses SQL daily averages per sensor over the `measurement` view (cheap over years),
        then takes the median across the sensors reporting each day so one noisy unit
        can't swing the area. PM readings over AREA_PM_MAX are left out of the averages."""
        cap = AREA_PM_MAX if field.startswith("pm") else None
        pool: dict = defaultdict(list)
        for sid in sids:
            for day, val in store.read_model.daily(conn, "air", sid, field, since=since, max_value=cap).items():
                pool[day].append(val)
        return [(d, round(statistics.median(v), 1), len(v)) for d, v in sorted(pool.items())]

    def _area_trend(daily, field: str):
        """Canonical linear_trend over the pooled daily medians (one point per day)."""
        from types import SimpleNamespace

        from airwv.analysis import linear_trend
        rows = [SimpleNamespace(**{"ts": datetime.combine(d, time.min), field: m})
                for d, m, _ in daily]
        return linear_trend(rows, field=field, min_days=14)

    @app.get("/api/areas")
    def areas(field: str = "pm2_5", days: int = 180):
        """Current rollup + trend per WV region, over community sensors. Reads the
        unified views (the `area` filter and measurement window /api/query uses);
        tests/snapshots/api_areas.json pins the output. Aggregates history, so cached
        briefly."""
        from airwv.analysis import is_worsening

        if field not in AREA_FIELDS:
            raise HTTPException(status_code=400,
                                detail=f"per-area rollups cover {sorted(AREA_FIELDS)} "
                                       f"(ozone is reference-only)")
        key = (field, days)
        import time as _t
        hit = _areas_cache.get(key)
        if hit and _t.time() - hit[0] < 300:
            return hit[1]

        since = datetime.utcnow() - timedelta(days=days)
        is_pm = field.startswith("pm")
        rm = store.read_model
        out = []
        with rm.connect() as conn:
            groups = _region_groups(conn)
            latest = {ref: v[field]["value"] for ref, v in
                      rm.latest(conn, "air", [s for g in groups.values() for s in g], field).items()}
            dailies = {region: _area_daily(conn, sids, field, since) for region, sids in groups.items()}
        for region, sids in groups.items():
            cur = [(sid, latest[sid]) for sid in sids
                   if sid in latest and not (is_pm and latest[sid] > AREA_PM_MAX)]
            daily = dailies[region]
            t = _area_trend(daily, field)
            value = round(statistics.median([v for _, v in cur]), 1) if cur else None
            worst = max(cur, key=lambda p: p[1]) if cur else None
            out.append({
                "region": region,
                "sensor_count": len(sids),
                "reporting": len(cur),
                "value": value,
                "color": _pm25_color(value) if (is_pm and value is not None) else "#9aa0a6",
                "max_value": round(worst[1], 1) if worst else None,
                "max_sensor": names.get(worst[0], worst[0]) if worst else None,
                "trend": {"direction": t.direction, "pct_change": t.pct_change,
                          "r": t.r, "n_days": t.n_days, "watch": is_worsening(t)},
            })
        # worst air first (None values sink to the bottom)
        out.sort(key=lambda a: (a["value"] is None, -(a["value"] or 0)))
        payload = {"field": field, "areas": out}
        _areas_cache[key] = (_t.time(), payload)
        return payload

    @app.get("/api/areas/series")
    def area_series(region: str, field: str = "pm2_5", days: int = 180):
        if field not in AREA_FIELDS:
            raise HTTPException(status_code=400, detail="unsupported field for area series")
        since = datetime.utcnow() - timedelta(days=days)
        with store.read_model.connect() as conn:
            sids = _region_groups(conn, region).get(region, [])
            daily = _area_daily(conn, sids, field, since) if sids else []
        t = _area_trend(daily, field)
        return {
            "region": region, "field": field, "sensor_count": len(sids),
            "points": [{"date": d.isoformat(), "value": m, "n": n} for d, m, n in daily],
            "trend": {"direction": t.direction, "pct_change": t.pct_change,
                      "r": t.r, "n_days": t.n_days, "first": t.first, "last": t.last},
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

    _sources_cache: dict = {}

    def _enriched_sources() -> dict:
        """Deduped + compliance-tagged source facilities (see sources_enrich). Cached —
        the underlying files are static, so we merge once per process."""
        if "data" not in _sources_cache:
            import json

            from airwv.sources_enrich import enrich_sources
            base = Path(__file__).parent.parent / "data"
            raw = json.loads((base / "sources.json").read_text(encoding="utf-8"))
            try:
                echo = json.loads((base / "echo_facilities.json").read_text(encoding="utf-8")).get("facilities", [])
            except Exception:
                echo = []
            merged = enrich_sources(raw.get("sources", []), echo)
            _sources_cache["data"] = {
                "tier": raw.get("tier"), "disclaimer": raw.get("disclaimer"),
                "source": raw.get("source"), "sources": merged,
                "raw_count": len(raw.get("sources", [])), "merged_count": len(merged),
            }
        return _sources_cache["data"]

    @app.get("/api/sources")
    def sources():
        try:
            return _enriched_sources()
        except Exception:
            return {"tier": "documented", "disclaimer": "", "sources": []}

    @app.get("/api/facilities")
    def facilities(status: str | None = None, program: str | None = None):
        """WV major regulated facilities + EPA ECHO compliance status. Optional
        filters: status=significant_violation|violation|compliant, program=air|water|…"""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "echo_facilities.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"facilities": [], "summary": {}, "source": "", "fetched_at": None}
        facs = data.get("facilities", [])
        summary = {"total": len(facs),
                   "significant_violation": sum(f["status"] == "significant_violation" for f in facs),
                   "violation": sum(f["status"] == "violation" for f in facs),
                   "compliant": sum(f["status"] == "compliant" for f in facs)}
        if status:
            facs = [f for f in facs if f["status"] == status]
        if program:
            facs = [f for f in facs if program in (f.get("programs") or [])]
        return {"facilities": facs, "summary": summary,
                "source": data.get("source"), "scope": data.get("scope"),
                "disclaimer": data.get("disclaimer"), "fetched_at": data.get("fetched_at")}

    @app.get("/api/dep-permits")
    def dep_permits(stage: str | None = None, county: str | None = None):
        """WV DEP oil & gas permit pipeline (requested / approved / under construction).
        Optional filters: stage=requested|approved|construction, county=<name>."""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "dep_permits.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"permits": [], "summary": {}, "source": "", "fetched_at": None}
        permits = data.get("permits", [])
        summary = {"total": len(permits),
                   "requested": sum(p["stage"] == "requested" for p in permits),
                   "approved": sum(p["stage"] == "approved" for p in permits),
                   "construction": sum(p["stage"] == "construction" for p in permits)}
        if stage:
            permits = [p for p in permits if p["stage"] == stage]
        if county:
            permits = [p for p in permits if (p.get("county") or "").lower() == county.lower()]
        return {"permits": permits, "summary": summary,
                "source": data.get("source"), "scope": data.get("scope"),
                "partner_note": data.get("partner_note"),
                "disclaimer": data.get("disclaimer"), "fetched_at": data.get("fetched_at")}

    @app.get("/api/dep-mining")
    def dep_mining(stage: str | None = None, kind: str | None = None):
        """WV DEP active + upcoming mining permits. Optional filters:
        stage=new|active|inactive, kind=<mining type substring>."""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "dep_mining.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"mines": [], "summary": {}, "source": "", "fetched_at": None}
        mines = data.get("mines", [])
        summary = {"total": len(mines),
                   "new": sum(m["stage"] == "new" for m in mines),
                   "active": sum(m["stage"] == "active" for m in mines),
                   "inactive": sum(m["stage"] == "inactive" for m in mines),
                   "acres_disturbed": round(sum(m.get("acres_disturbed") or 0 for m in mines))}
        if stage:
            mines = [m for m in mines if m["stage"] == stage]
        if kind:
            mines = [m for m in mines if kind.lower() in (m.get("type") or "").lower()]
        return {"mines": mines, "summary": summary,
                "source": data.get("source"), "scope": data.get("scope"),
                "partner_note": data.get("partner_note"),
                "disclaimer": data.get("disclaimer"), "fetched_at": data.get("fetched_at")}

    @app.get("/api/coal-npdes")
    def coal_npdes(min_outlets: int = 0, impaired: bool = False):
        """WV coal-mine water discharge permits (NPDES), aggregated by permit and
        joined to the 2016 303(d) impaired-streams list. min_outlets filters small
        ones; impaired=true keeps only permits on an impaired stream."""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "coal_npdes.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"permits": [], "summary": {}, "source": "", "fetched_at": None}
        permits = data.get("permits", [])
        cause_counts: dict = defaultdict(int)
        for p in permits:
            for cse in p.get("impairment_causes", []):
                cause_counts[cse] += 1
        summary = {"permits": len(permits),
                   "outlets": sum(p.get("outlets", 0) for p in permits),
                   "impaired": sum(1 for p in permits if p.get("impaired")),
                   "top_causes": sorted(cause_counts.items(), key=lambda kv: -kv[1])[:6]}
        if min_outlets:
            permits = [p for p in permits if p.get("outlets", 0) >= min_outlets]
        if impaired:
            permits = [p for p in permits if p.get("impaired")]
        return {"permits": permits, "summary": summary,
                "source": data.get("source"), "scope": data.get("scope"),
                "partner_note": data.get("partner_note"),
                "disclaimer": data.get("disclaimer"), "fetched_at": data.get("fetched_at")}

    _wells_file: dict = {}

    def _abandoned_wells_data() -> dict | None:
        """The parsed abandoned-wells file (2.4MB), re-read only when it changes on disk."""
        import json

        path = Path(__file__).parent.parent / "data" / "abandoned_wells.json"
        try:
            mtime = path.stat().st_mtime
            if _wells_file.get("mtime") != mtime:
                _wells_file.update(mtime=mtime, data=json.loads(path.read_text(encoding="utf-8")))
            return _wells_file["data"]
        except Exception:
            return None

    @app.get("/api/abandoned-wells")
    def abandoned_wells(response: Response, orphan_only: bool = False, near_homes: bool = False,
                        bbox: str | None = None):
        """WV abandoned oil/gas wells (WV DEP). orphan_only keeps the ~4,700 with no
        known operator; near_homes keeps those within ~200 m of a building; bbox
        (west,south,east,north) keeps those inside a map view. Large — loaded lazily by
        the map layer. The count fields stay statewide."""
        box = _parse_bbox(bbox)
        data = _abandoned_wells_data()
        if data is None:
            return {"wells": [], "count": 0, "orphans": 0}
        response.headers.update(_LAYER_CACHE)
        wells = data.get("wells", [])
        if orphan_only:
            wells = [w for w in wells if w.get("orphan")]
        if near_homes:
            wells = [w for w in wells if w.get("near_homes")]
        if box:
            wells = [w for w in wells if _in_bbox(w.get("lat"), w.get("lon"), box)]
        return {"wells": wells, "count": data.get("count", len(wells)),
                "orphans": data.get("orphans", 0),
                "near_homes": data.get("near_homes", 0),
                "orphans_near_homes": data.get("orphans_near_homes", 0),
                "near_homes_m": data.get("near_homes_m", 200),
                "source": data.get("source"),
                "disclaimer": data.get("disclaimer"), "fetched_at": data.get("fetched_at")}

    @app.get("/api/well-backlog")
    def well_backlog():
        """The orphan-well plugging backlog by county (from our abandoned-well data).
        Rate/funding context is documented in the UI, not derived here."""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "abandoned_wells.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"orphans_total": 0, "orphans_near_homes": 0, "counties": []}
        roll: dict = defaultdict(lambda: {"orphans": 0, "orphans_near_homes": 0})
        for w in data.get("wells", []):
            if not w.get("orphan"):
                continue
            c = w.get("county") or "Unknown"
            roll[c]["orphans"] += 1
            if w.get("near_homes"):
                roll[c]["orphans_near_homes"] += 1
        counties = sorted(({"county": k, **v} for k, v in roll.items()),
                          key=lambda r: -r["orphans"])
        return {"orphans_total": data.get("orphans", 0),
                "orphans_near_homes": data.get("orphans_near_homes", 0),
                "counties": counties, "fetched_at": data.get("fetched_at")}

    @app.get("/api/sdwa")
    def sdwa(health_only: bool = False, community_only: bool = False, county: str | None = None):
        """WV public drinking-water systems + SDWA violation status, with a county
        rollup for the map. Filters: health_only, community_only, county."""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "sdwa_systems.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"systems": [], "counties": [], "summary": {}, "source": "", "fetched_at": None}
        systems = data.get("systems", [])
        summary = {
            "systems": len(systems),
            "community": sum(1 for s in systems if s.get("community")),
            "health_violation": sum(1 for s in systems if s.get("health_violation")),
            "population_affected": sum(s.get("population", 0) for s in systems if s.get("health_violation")),
        }
        # county rollup (always over the full set, for the map + ranking)
        roll: dict = {}
        for s in systems:
            c = s.get("county")
            if not c:
                continue
            r = roll.setdefault(c, {"county": c, "lat": s.get("lat"), "lon": s.get("lon"),
                                    "systems": 0, "health": 0, "serious": 0, "pop_affected": 0})
            r["systems"] += 1
            if s.get("health_violation"):
                r["health"] += 1
                r["pop_affected"] += s.get("population", 0)
            if s.get("serious_violator"):
                r["serious"] += 1
        counties = sorted(roll.values(), key=lambda r: -r["health"])
        if health_only:
            systems = [s for s in systems if s.get("health_violation")]
        if community_only:
            systems = [s for s in systems if s.get("community")]
        if county:
            systems = [s for s in systems if (s.get("county") or "").lower() == county.lower()]
        return {"systems": systems, "counties": counties, "summary": summary,
                "source": data.get("source"), "scope": data.get("scope"),
                "disclaimer": data.get("disclaimer"), "fetched_at": data.get("fetched_at")}

    @app.get("/api/nrc-spills")
    def nrc_spills(reached_water: bool = False, year: int | None = None):
        """WV spill/release reports from the National Response Center. Optional
        filters: reached_water=true (only water-reaching), year=YYYY."""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "nrc_spills.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"spills": [], "summary": {}, "source": "", "fetched_at": None}
        spills = data.get("spills", [])
        summary = {"total": len(spills),
                   "reached_water": sum(1 for s in spills if s.get("reached_water"))}
        if reached_water:
            spills = [s for s in spills if s.get("reached_water")]
        if year:
            spills = [s for s in spills if (s.get("date") or "").startswith(str(year))]
        return {"spills": spills, "summary": summary,
                "source": data.get("source"), "scope": data.get("scope"),
                "disclaimer": data.get("disclaimer"), "fetched_at": data.get("fetched_at")}

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

    @app.get("/api/data-centers")
    def data_centers_layer(lat: float | None = None, lon: float | None = None, km: float = 16.0,
                           full: bool = False):
        """Data centers and crypto-mining sites (hand-researched, data/data_centers.json),
        each with its basic-view card (what we know / what's not disclosed for water,
        energy, noise and purpose). With lat/lon: ``near`` = mapped sites within ``km``,
        nearest first, and ``in_county`` = sites in the nearest county with no public
        coordinates. Without: every site in ``near``. Plus how to ask for what's missing.
        ``full=true`` adds each site's whole record (``record``) for the advanced view."""
        import json

        from airwv import data_centers as dcs
        from airwv.wvgeo import WV_COUNTY_CENTROID

        rm = store.read_model
        with rm.connect() as conn:
            prov = rm.provenance(conn, ["data_centers"]).get("data_centers", {})
            dockets = json.loads(conn.exec_driver_sql(
                "SELECT coalesce(json_extract(doc, '$.dockets'), '[]') FROM layer_doc "
                "WHERE layer = 'data_centers'").scalar() or "[]")
            county, in_county = None, []
            if lat is None or lon is None:
                near = rm.sites(conn, layer="data_centers")
            else:
                km = min(max(km, 1.0), 50.0)
                near = rm.sites_near(conn, lat, lon, km * 0.621371, layer="data_centers")
                county = min(WV_COUNTY_CENTROID,
                             key=lambda c: _hav_mi(lat, lon, *WV_COUNTY_CENTROID[c]), default=None)
                in_county = [s for s in rm.sites(conn, layer="data_centers", where="lat IS NULL")
                             if county in [c.strip() for c in (s["county"] or "").split("/")]]

        def row(s):
            out = dcs.card(s["attrs"])
            out.update(lat=s["lat"], lon=s["lon"], mi=round(s["d"], 1) if s["d"] is not None else None)
            if full:
                out["record"] = s["attrs"]
            return out

        return {"place": {"lat": lat, "lon": lon, "km": km} if lat is not None and lon is not None else None,
                "county": county, "near": [row(s) for s in near], "in_county": [row(s) for s in in_county],
                "how_to_ask": dcs.HOW_TO_ASK, "dockets": dockets, **prov}

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
                "monitor (EPA AirNow live + AirData finalized history), correlating daily PM2.5 over "
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

    _water_sites_cache: dict = {}

    @app.get("/api/water/sites")
    def water_sites(response: Response, bbox: str | None = None):
        """Every water site with its latest value per parameter (~1MB statewide);
        bbox (west,south,east,north) keeps those inside a map view. Cached 5 min —
        the USGS/WQP collectors run hourly."""
        import time as _t

        box = _parse_bbox(bbox)
        response.headers.update(_SHORT_CACHE)
        hit = _water_sites_cache.get("v")
        if not (hit and _t.time() - hit[0] < 300):
            hit = _water_sites_cache["v"] = (_t.time(), store.water_sites())
        sites = hit[1]
        if box:
            sites = [s for s in sites if _in_bbox(s.get("lat"), s.get("lon"), box)]
        return {"sites": sites}

    @app.get("/api/water/near")
    def water_near(lat: float, lon: float, km: float = 5.0, limit: int = 6):
        """Water sample sites near a point with their latest measured values —
        used to attach measured water quality to a coal discharger / facility. Reads the
        unified views (the water sites /api/query?medium=water returns);
        tests/snapshots/api_water_near.json pins the output."""
        km, limit = min(km, 25), min(limit, 20)
        rm = store.read_model
        with rm.connect() as conn:
            sites = rm.sites_near(conn, lat, lon, km * 0.621371, layer="water_readings",
                                  limit=10_000, bbox_km=km)
            sites = sorted(sites, key=lambda s: (round(s["d"], 1), s["ref"]))[:limit]
            latest = rm.latest(conn, "water", [s["ref"] for s in sites])
        return {"sites": [{
            "site_id": s["ref"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
            "latest": {p: {**v, "ts": v["ts"] and datetime.fromisoformat(v["ts"]).isoformat()}
                       for p, v in latest.get(s["ref"], {}).items()},
            "mi": round(s["d"], 1)} for s in sites]}

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

    @app.get("/api/reports/{report_id}/photo")
    def report_photo(report_id: int):
        """Report photo — served only once a maintainer has approved the image
        (photo_ok) on a publicly-visible report. Held images stay private."""
        r = store.get_report(report_id)
        if (not r or r.stage not in ("published_unverified", "confirmed")
                or not r.photo_path or not r.photo_ok):
            raise HTTPException(status_code=404, detail="no photo")
        path = REPORT_PHOTO_DIR / r.photo_path
        if not path.exists():
            raise HTTPException(status_code=404, detail="missing file")
        return FileResponse(path)

    @app.get("/field", response_class=HTMLResponse)
    def field_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="field.html",
                                          context={"mode": "field"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, place: str | None = None, lat: str | None = None,
              lon: str | None = None, statewide: str | None = None):
        # a place in the URL (?place= or ?lat=&lon=) makes Home "my place now" (place.js keeps
        # it in the URL); otherwise statewide. overview.html sends a browser with a saved place
        # here with it, unless ?statewide=1 asks for the statewide view.
        if not statewide and (place or (lat and lon)):
            return TEMPLATES.TemplateResponse(request=request, name="place.html",
                                              context={"mode": "place", "home": True})
        return TEMPLATES.TemplateResponse(request=request, name="overview.html",
                                          context={"mode": "overview"})

    @app.get("/air", response_class=HTMLResponse)
    def air(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="dashboard.html",
                                          context={"mode": "air"})

    @app.get("/analysis")
    def analysis_redirect():   # kept so old links / bookmarks still work
        return RedirectResponse("/air", status_code=301)

    @app.get("/learn", response_class=HTMLResponse)
    def learn(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="learn.html",
                                          context={"mode": "learn"})

    @app.get("/events", response_class=HTMLResponse)
    def events_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="events.html",
                                          context={"mode": "events"})

    @app.get("/spills", response_class=HTMLResponse)
    def spills_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="spills.html",
                                          context={"mode": "spills"})

    @app.get("/facilities", response_class=HTMLResponse)
    @app.get("/sources", response_class=HTMLResponse)   # old name, kept for links / bookmarks
    def sources_page(request: Request):
        # Google Street View Static API key (optional) — front-of-business photos when set.
        return TEMPLATES.TemplateResponse(request=request, name="sources.html",
                                          context={"mode": "sources",
                                                   "sv_key": os.environ.get("AIRWV_GOOGLE_MAPS_KEY", "")})

    @app.get("/alerts", response_class=HTMLResponse)
    def alerts_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="alerts.html",
                                          context={"mode": "alerts"})

    @app.get("/nearby", response_class=HTMLResponse)
    def nearby_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="near_me.html",
                                          context={"mode": "nearby"})

    @app.get("/place", response_class=HTMLResponse)
    def place_page(request: Request):
        # "my place now" (basic view, DIRECTION.md §2): the page reads lat/lon/km/name
        # from its URL and builds every line from /api/query
        return TEMPLATES.TemplateResponse(request=request, name="place.html",
                                          context={"mode": "place"})

    @app.get("/explore", response_class=HTMLResponse)
    def explore_page(request: Request):
        # query builder v1 (DIRECTION.md §5.2): a form over /api/query; the form lives in the
        # URL, and the page shows the table, a CSV download and the API URL for the same answer
        return TEMPLATES.TemplateResponse(request=request, name="explore.html",
                                          context={"mode": "explore",
                                                   "counties": sorted(WV_COUNTY_CENTROID)})

    @app.get("/site/{kind}/{ref:path}", response_class=HTMLResponse)
    def site_page(request: Request, kind: str, ref: str, view: str = "basic", param: str | None = None):
        """The single home of one site (docs/DIRECTION.md), for every kind in the read
        model's ``site`` view (``SITE_KINDS``). Measured kinds get a recent-readings chart from
        the /api/query series mode; data centers keep their own card ("what we know / what's
        not disclosed"); permits show their stage and 303(d) records; the rest their public
        records. ``view=adv`` is the full record."""
        from airwv import data_centers as dcs

        if kind not in SITE_KINDS:
            raise HTTPException(status_code=404, detail="no such site kind")
        view = "adv" if view == "adv" else "basic"
        if kind in SERIES_KINDS:
            return _measured_site_page(request, kind, ref, param)
        if kind == "permit":
            return _permit_site_page(request, ref, view)
        if kind != "data_center":
            return _generic_site_page(request, kind, ref, view)
        api = data_centers_layer()
        rm = store.read_model
        with rm.connect() as conn:
            found = rm.sites(conn, layer="data_centers", where="ref = :ref", params={"ref": ref})
        if not found:
            raise HTTPException(status_code=404, detail="unknown data center")
        dc = found[0]["attrs"]
        return TEMPLATES.TemplateResponse(request=request, name="site.html", context={
            "mode": "site", "kind": kind, "view": view,
            "dc": dc, "card": dcs.card(dc), "cited": dcs.cited, "ask": dcs.HOW_TO_ASK, "dockets": api["dockets"],
            "prov": {k: api.get(k) for k in ("source", "disclaimer", "fetched_at")}})

    def _generic_site_page(request: Request, kind: str, ref: str, view: str):
        rm = store.read_model
        with rm.connect() as conn:
            site = rm.site(conn, f"{kind}:{ref}")
            if site is None:
                raise HTTPException(status_code=404, detail="unknown site")
            records = rm.records_for(conn, [site["site_id"]]).get(site["site_id"], [])
            prov = rm.provenance(conn, [site["layer"]]).get(site["layer"], {})
        return TEMPLATES.TemplateResponse(request=request, name="site.html", context={
            "mode": "site", "kind": kind, "view": view, "site": site, "label": SITE_KINDS[kind],
            "records": records, "record_labels": SITE_RECORD_LABELS, "prov": prov})

    def _measured_site_page(request, kind, ref, param):
        """A sensor, monitor, gauge or sample site: what it measures, its latest reading and a
        chart of the last ``SERIES_KINDS[kind]`` days up to that reading (site.js fetches
        the rows from /api/query series mode, so the chart, CSV and API link agree)."""
        rm = store.read_model
        with rm.connect() as conn:
            site = rm.site(conn, f"{kind}:{ref}")
            if site is None and kind == "reference_monitor" and len(ref) == 9 and ref.isdigit():
                # /api/sensors lists AirNow monitors by 9-digit AQSID; the read model keeps
                # the dashed AQS form when reference_monitors.json lists the same monitor
                site = rm.site(conn, f"{kind}:{ref[:2]}-{ref[2:5]}-{ref[5:]}")
            if site is None:
                raise HTTPException(status_code=404, detail="no site with that id")
            measured = rm.parameters(conn, site["medium"], measured_refs(site))
            prov = rm.provenance(conn, [site["layer"]]).get(site["layer"]) or {}
        names = [m["parameter"] for m in measured]
        chosen = next((m for m in measured if m["parameter"] == param), None) \
            or next((m for m in measured if m["parameter"] == "pm2_5"), None) \
            or (measured[0] if measured else None)
        window = None
        if chosen:
            last = date.fromisoformat(str(chosen["last"])[:10])
            q = {"site_id": site["site_id"], "param": chosen["parameter"],
                 "from": (last - timedelta(days=SERIES_KINDS[kind])).isoformat(), "to": last.isoformat()}
            window = {**q, "days": SERIES_KINDS[kind], "api": "/api/query?" + urlencode(q),
                      "csv": "/api/query?" + urlencode(q | {"format": "csv"})}
        return TEMPLATES.TemplateResponse(request=request, name="site_measured.html", context={
            "mode": "site", "kind": kind, "site": site, "measured": measured, "names": names,
            "chosen": chosen, "window": window, "label": SERIES_KIND_LABELS[kind],
            "plabel": lambda p: METRIC_LABELS.get(p, p.replace("_", " ")), "prov": prov})

    def _permit_site_page(request, ref, view):
        """A coal discharge, DEP mining or oil & gas permit: what it is, its permit stage and,
        for coal discharges, the 303(d) impaired streams its outlets sit on (the read model's
        ``record`` rows), with the layer's caveat. ``view=adv`` lists every field."""
        rm = store.read_model
        with rm.connect() as conn:
            site = rm.site(conn, f"permit:{ref}")
            if site is None:
                raise HTTPException(status_code=404, detail="no permit with that id")
            # an oil & gas API number can list identical rows; keep each record once
            records = list({tuple(r.values()): r for r in
                            rm.records_for(conn, [site["site_id"]]).get(site["site_id"], [])}.values())
            prov = rm.provenance(conn, [site["layer"]]).get(site["layer"]) or {}
        a = site["attrs"]
        return TEMPLATES.TemplateResponse(request=request, name="site_permit.html", context={
            "mode": "site", "kind": "permit", "view": view, "site": site, "a": a,
            "what": PERMIT_LAYERS[site["layer"]], "stage": PERMIT_STAGE_LABELS.get(a.get("stage")),
            "stage_rec": next((r for r in records if r["kind"] == "permit_stage"), None),
            "impaired": next((r for r in records if r["kind"] == "impaired_303d"), None),
            "records": records, "prov": prov})

    @app.get("/data", response_class=HTMLResponse)
    def data_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="data.html",
                                          context={"mode": "data", "dump_info": dumps.summary()})

    @app.get("/data/dumps/{name}")
    def data_dump(name: str):
        """The nightly public dumps (dumps.py): gzipped CSV of the read views + manifest.json.
        Only the fixed file names; 404 until the first nightly run has written them."""
        path = dumps.default_dir() / name
        if name not in dumps.FILES or not path.is_file():
            raise HTTPException(status_code=404, detail="no such dump (yet)")
        media = "application/json" if name.endswith(".json") else "application/gzip"
        return FileResponse(path, media_type=media, filename=name,
                            headers={"Cache-Control": "public, max-age=3600"})

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
    store = Store(url)
    # Self-heal the schema on web boot: create_all is idempotent and _ensure_columns
    # only ever *adds* missing nullable columns, so a deploy that introduces a new
    # field works without a manual migration step. Best-effort — never block startup.
    try:
        store.create_schema()
    except Exception:  # pragma: no cover - a read-only/locked DB shouldn't crash serving
        import logging
        logging.getLogger(__name__).exception("schema self-heal on startup failed; continuing")
    return store


app = create_app(_default_store())
