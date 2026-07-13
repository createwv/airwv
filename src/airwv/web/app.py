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
import os
import re
import secrets
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from airwv.analysis import detrend_events, hour_of_day_profile
from airwv.export_utils import readings_to_csv
from airwv.notify.chat import chat_notifier_from_env
from airwv.notify.email import email_enabled, send_email
from airwv.reporting import DOMAINS, client_ip, ip_hash, jitter, load_facility_triggers, screen
from airwv.storage import Store

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


class AlertSignupIn(BaseModel):
    email: str
    sensor_id: str | None = None           # None / "" = any WV sensor
    level: str = "unhealthy"               # sensitive | unhealthy | veryunhealthy
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

# Fields community sensors measure — the ones that make sense to roll up by area
# (ozone/aqi are reference-only or derived, so they're excluded from area rollups).
AREA_FIELDS = {"pm2_5", "pm1_0", "pm10", "voc", "temperature", "humidity"}


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
        to symptoms. Not medical advice."""
        km = min(max(km, 1.0), 25.0)
        radius_mi = km * 0.621371

        def near_pts(items, cap, make, latk="lat", lonk="lon"):
            out = []
            for it in items:
                la, lo = it.get(latk), it.get(lonk)
                if la is None or lo is None:
                    continue
                d = _hav_mi(lat, lon, la, lo)
                if d <= radius_mi:
                    out.append((d, it))
            out.sort(key=lambda x: x[0])
            return [make(it, round(d, 1)) for d, it in out[:cap]]

        haz: list[dict] = []

        # abandoned / orphan gas wells → gas (H2S / natural gas)
        haz += near_pts(_data_json("abandoned_wells.json").get("wells", []), 12, lambda w, d: {
            "category": "gas", "icon": "🛢️",
            "label": ("Orphan" if w.get("orphan") else "Abandoned") + " gas well",
            "detail": (f"{w['nearest_building_m']} m from a building" if w.get("near_homes")
                       else (w.get("county") and f"{w['county']} County")),
            "flag": "orphan" if w.get("orphan") else None, "mi": d, "lat": w["lat"], "lon": w["lon"],
            "link": f"http://www.wvgs.wvnet.edu/oginfo/pipeline/pipeline2.asp?txtsearchapi=47{(w.get('id') or '').replace('-', '')}",
            "report": True})

        # EPA ECHO major facilities → air or water by program; violations flagged
        def _fac(f, d):
            water = any(p in (f.get("programs") or []) for p in ("water", "drinking-water"))
            return {"category": "water" if water and "air" not in (f.get("programs") or []) else "air",
                    "icon": "⚖️", "label": f.get("name"),
                    "detail": (f.get("compliance_status") or "").strip() or "regulated facility",
                    "flag": "violation" if f.get("status") in ("significant_violation", "violation") else None,
                    "mi": d, "lat": f["lat"], "lon": f["lon"], "link": f.get("echo_url"), "report": True}
        haz += near_pts(_data_json("echo_facilities.json").get("facilities", []), 8, _fac)

        # curated TRI / documented pollution sources → air/chemical
        haz += near_pts(_data_json("sources.json").get("sources", []), 8, lambda s, d: {
            "category": "air", "icon": "🏭", "label": s.get("name"),
            "detail": s.get("type") or s.get("operator"), "flag": None, "mi": d,
            "lat": s["lat"], "lon": s["lon"], "link": f"/sources#facility={s.get('name', '')}", "report": True})

        # coal-mine water discharges → water
        haz += near_pts(_data_json("coal_npdes.json").get("permits", []), 6, lambda p, d: {
            "category": "water", "icon": "⛏️", "label": f"Coal discharge — {p.get('operator')}",
            "detail": (f"impaired for {', '.join(p.get('impairment_causes', [])[:2])}"
                       if p.get("impaired") else f"{p.get('outlets')} outlets"),
            "flag": "impaired" if p.get("impaired") else None, "mi": d,
            "lat": p["lat"], "lon": p["lon"], "link": p.get("effluent_url"), "report": False})

        # active mining permits → air (dust) / land
        haz += near_pts(_data_json("dep_mining.json").get("mines", []), 6, lambda m, d: {
            "category": "air", "icon": "⛏️", "label": f"{m.get('type') or 'Mining'} — {m.get('operator')}",
            "detail": (f"{m.get('acres_disturbed')} acres disturbed" if m.get("acres_disturbed") else m.get("permit_status")),
            "flag": None, "mi": d, "lat": m["lat"], "lon": m["lon"], "link": None, "report": True})

        # NRC reported spills → chemical (and water when they reached water)
        haz += near_pts(_data_json("nrc_spills.json").get("spills", []), 6, lambda s, d: {
            "category": "water" if s.get("reached_water") else "chemical", "icon": "🛢️",
            "label": "Reported spill — " + (", ".join(m["name"] for m in s.get("materials", [])[:2]) or "release"),
            "detail": f"{s.get('date') or ''}{' · reached water' if s.get('reached_water') else ''}".strip(" ·"),
            "flag": "spill", "mi": d, "lat": s["lat"], "lon": s["lon"],
            "link": "https://nrc.uscg.mil/", "report": False})

        # community sensors → sensor (VOC / PM2.5 right now)
        latest_pm = store.latest_value_per_sensor("pm2_5")
        latest_voc = store.latest_value_per_sensor("voc")
        sensor_items = [{"sid": sid, "lat": c[0], "lon": c[1]} for sid, c in coords.items() if sid in regions]
        haz += near_pts(sensor_items, 4, lambda s, d: {
            "category": "sensor", "icon": "📡", "label": names.get(s["sid"], s["sid"]) + " sensor",
            "detail": " · ".join(x for x in [
                (latest_pm.get(s["sid"]) is not None and f"PM2.5 {round(latest_pm[s['sid']], 1)}"),
                (latest_voc.get(s["sid"]) is not None and f"VOC {round(latest_voc[s['sid']], 0):g}")] if x) or "community sensor",
            "flag": None, "mi": d, "lat": s["lat"], "lon": s["lon"],
            "link": "/air", "report": False})

        # measured water sample sites → water
        for s in store.water_near(lat, lon, km=km, limit=4):
            keys = [k for k in ("selenium", "iron", "manganese", "sulfate", "conductance", "ph", "ecoli") if k in s.get("latest", {})]
            haz.append({"category": "water", "icon": "💧", "label": s["name"] + " (water sample)",
                        "detail": ", ".join(f"{k} {s['latest'][k]['value']}" for k in keys[:3]) or "sample site",
                        "flag": None, "mi": s["mi"], "lat": s["lat"], "lon": s["lon"],
                        "link": "/water", "report": False})

        # drinking-water systems with a health-based violation in the nearest county → water
        from airwv.wvgeo import WV_COUNTY_CENTROID
        near_county = min(WV_COUNTY_CENTROID,
                          key=lambda c: _hav_mi(lat, lon, *WV_COUNTY_CENTROID[c]), default=None)
        if near_county:
            for sy in _data_json("sdwa_systems.json").get("systems", []):
                if sy.get("county") == near_county and sy.get("health_violation"):
                    haz.append({"category": "water", "icon": "🚰",
                                "label": f"{sy.get('name')} (water system)",
                                "detail": f"health-based SDWA violation · serves {sy.get('population')}",
                                "flag": "violation", "mi": None, "lat": sy.get("lat"), "lon": sy.get("lon"),
                                "link": sy.get("dfr_url"), "report": False})

        cats: dict = defaultdict(int)
        for h in haz:
            cats[h["category"]] += 1
        return {"lat": lat, "lon": lon, "km": km, "county": near_county,
                "hazards": haz, "counts": dict(cats)}

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

        threshold, level_label = ALERT_LEVELS[body.level]
        sensor_id = (body.sensor_id or "").strip() or None
        area = (body.label or "").strip()[:120] or (names.get(sensor_id) if sensor_id else "any WV sensor")

        existing = store.find_subscription(target=email, field="pm2_5", sensor_id=sensor_id)
        if existing is not None:
            # Idempotent: refresh the level, re-arm confirmation if still pending.
            token = existing.token or secrets.token_urlsafe(24)
            confirmed = existing.confirmed_at is not None
        else:
            token = secrets.token_urlsafe(24)
            confirmed = False
            store.add_subscription(
                channel="email", target=email, kind="threshold",
                sensor_id=sensor_id, field="pm2_5", threshold=threshold,
                active=False, label=area, token=token,
            )

        base = _base_url(request)
        confirm_url = f"{base}/alerts/confirm?token={token}"
        unsub_url = f"{base}/alerts/unsubscribe?token={token}"
        sent = False
        if not confirmed and email_enabled():
            body_txt = (
                f"Thanks for signing up for AirWV air-quality alerts.\n\n"
                f"You asked to be emailed when PM2.5 near {area} reaches "
                f"“{level_label}” ({threshold:g} µg/m³).\n\n"
                f"Please confirm this address to turn the alerts on:\n{confirm_url}\n\n"
                f"If you didn't request this, ignore this email — nothing happens "
                f"until you confirm.\n\nUnsubscribe any time: {unsub_url}\n"
            )
            background.add_task(send_email, email,
                                "Confirm your AirWV air-quality alerts", body_txt)
            sent = True

        if confirmed:
            msg = "You're already subscribed and confirmed — we updated your alert level."
        elif sent:
            msg = "Almost there — check your email for a confirmation link to turn on alerts."
        else:
            # SMTP not live yet: we captured the sign-up as a waitlist entry.
            msg = ("You're on the list. Email alert delivery is still being finished — "
                   "we'll send a confirmation link the moment it goes live.")
        return {"ok": True, "confirmed": confirmed, "email_sent": sent,
                "level": level_label, "area": area, "message": msg}

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
            f"<p>Alerts are on. We'll email <b>{html.escape(sub.target)}</b> when PM2.5 near "
            f"<b>{html.escape(area)}</b> crosses your threshold — with quiet hours and rate limits "
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

    @app.get("/api/admin/subscriptions")
    def admin_subscriptions(_: bool = Depends(_require_admin)):
        """Alert sign-ups — including the pending/unconfirmed waitlist."""
        subs = store.list_subscriptions(active_only=False)
        return {"email_delivery": email_enabled(),
                "results": [{"id": s.id, "email": s.target, "channel": s.channel,
                             "sensor_id": s.sensor_id, "area": s.label,
                             "field": s.field, "threshold": s.threshold,
                             "active": s.active,
                             "confirmed": s.confirmed_at is not None,
                             "created_at": s.created_at.isoformat() if s.created_at else None}
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

    def _region_groups() -> dict[str, list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for sid, region in regions.items():
            groups[region or "Other"].append(sid)
        return groups

    def _area_daily(sids, field: str, since):
        """Pooled daily series across a region's sensors → sorted [(date, median, n)].
        Uses SQL daily averages per sensor (cheap over years), then takes the median
        across the sensors reporting each day so one noisy unit can't swing the area."""
        pool: dict = defaultdict(list)
        for sid in sids:
            for day, val in store.daily_avg(sid, field=field, since=since).items():
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
        """Current rollup + trend per WV region, over community sensors. Aggregates
        history, so cached briefly."""
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

        latest = store.latest_value_per_sensor(field)
        since = datetime.utcnow() - timedelta(days=days)
        is_pm = field.startswith("pm")
        out = []
        for region, sids in _region_groups().items():
            cur = [(sid, latest[sid]) for sid in sids
                   if sid in latest and not (is_pm and latest[sid] > 1000)]
            daily = _area_daily(sids, field, since)
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
        sids = _region_groups().get(region, [])
        since = datetime.utcnow() - timedelta(days=days)
        daily = _area_daily(sids, field, since) if sids else []
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

    @app.get("/api/sources")
    def sources():
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "sources.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"tier": data.get("tier"), "disclaimer": data.get("disclaimer"),
                    "source": data.get("source"), "sources": data.get("sources", [])}
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

    @app.get("/api/abandoned-wells")
    def abandoned_wells(orphan_only: bool = False, near_homes: bool = False):
        """WV abandoned oil/gas wells (WV DEP). orphan_only keeps the ~4,700 with no
        known operator; near_homes keeps those within ~200 m of a building. Large —
        loaded lazily by the map layer."""
        try:
            import json

            path = Path(__file__).parent.parent / "data" / "abandoned_wells.json"
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"wells": [], "count": 0, "orphans": 0}
        wells = data.get("wells", [])
        if orphan_only:
            wells = [w for w in wells if w.get("orphan")]
        if near_homes:
            wells = [w for w in wells if w.get("near_homes")]
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

    @app.get("/api/water/near")
    def water_near(lat: float, lon: float, km: float = 5.0, limit: int = 6):
        """Water sample sites near a point with their latest measured values —
        used to attach measured water quality to a coal discharger / facility."""
        return {"sites": store.water_near(lat, lon, km=min(km, 25), limit=min(limit, 20))}

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

    @app.get("/sources", response_class=HTMLResponse)
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

    @app.get("/data", response_class=HTMLResponse)
    def data_page(request: Request):
        return TEMPLATES.TemplateResponse(request=request, name="data.html",
                                          context={"mode": "data"})

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
