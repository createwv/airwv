"""Evaluate subscriptions against the latest readings and the public records.

Pure logic (no I/O): given active subscriptions and the newest reading per
sensor, decide which should fire — respecting the threshold, per-subscription
rate limiting, and local quiet hours. Record subscriptions (spills, violations)
fire on records that are new for their place since the last check, or back on
the list after dropping off it (:func:`evaluate_records`). Delivery is handled separately by the notify channels.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

from airwv.wvgeo import WV_COUNTY_CENTROID, canon_county

EASTERN = "America/New_York"

# Record subscriptions: the subscription's ``field`` names a rule over the ``record``
# view (storage/readmodel.py). ``county_placed`` layers mostly carry only a county, so
# those sites sit on the county centroid; such a record also matches a place inside
# that county (each record says whether it is county-placed).
RECORD_RULES = {
    "spills":     {"layer": "nrc_spills", "kinds": ("spill",), "county_placed": True,
                   "label": "reported spills"},
    "water":      {"layer": "sdwa_systems", "kinds": ("health_violation",), "county_placed": True,
                   "label": "drinking-water violations"},
    "violations": {"layer": "echo_facilities", "kinds": ("violation",), "county_placed": False,
                   "label": "facility violations"},
}

# Rules whose records can leave the data and come back (a spill report doesn't "return").
# A record gone at least this long and then listed again alerts as returned; a shorter
# absence is treated as a blip in the data file and stays silent.
RETURNING_RULES = ("water", "violations")
RETURN_AFTER_DAYS = 30


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


@dataclass
class Alert:
    subscription_id: int
    channel: str
    target: str
    sensor_id: str
    field: str
    value: float
    threshold: float
    ts: datetime
    kind: str = "threshold"  # "threshold" | "trend" | "record"
    label: str | None = None                                # the subscription's place
    records: list[dict] = dc_field(default_factory=list)     # record alerts: what's new
    unsubscribe_url: str | None = None


def _local_hour(now: datetime, tzname: str) -> int:
    aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(tzname)).hour


def _in_quiet_hours(hour: int, start, end) -> bool:
    if start is None or end is None:
        return False
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps midnight


def _in_radius(sub, sid: str, sensor_coords) -> bool:
    """When a subscription is area-scoped (center + radius, no fixed sensor),
    keep only sensors whose coordinates fall inside the radius. Sensors with
    unknown coordinates are excluded so an area alert never fires on a mystery point."""
    center = (getattr(sub, "center_lat", None), getattr(sub, "center_lon", None))
    radius = getattr(sub, "radius_km", None)
    if center[0] is None or center[1] is None or not radius:
        return True  # not area-scoped — no geo restriction
    coord = (sensor_coords or {}).get(sid)
    if not coord or coord[0] is None or coord[1] is None:
        return False
    return _haversine_km(center[0], center[1], coord[0], coord[1]) <= radius


def evaluate(subscriptions, latest_by_sensor, now: datetime, tzname: str = EASTERN,
             trends=None, sensor_coords=None) -> list[Alert]:
    """Return the alerts that should fire right now.

    ``trends`` maps ``(sensor_id, field) -> Trend`` and is only needed for
    trend-kind subscriptions (fire when a field is rising by >= threshold percent).
    ``sensor_coords`` maps ``sensor_id -> (lat, lon)`` and is only needed for
    area-scoped subscriptions (center + radius instead of a single sensor).
    """
    hour = _local_hour(now, tzname)
    trends = trends or {}
    alerts: list[Alert] = []

    for sub in subscriptions:
        if not sub.active:
            continue
        if _in_quiet_hours(hour, sub.quiet_start, sub.quiet_end):
            continue
        if sub.last_notified_at is not None:
            elapsed = (now - sub.last_notified_at).total_seconds()
            if elapsed < sub.min_interval_seconds:
                continue

        if is_record_sub(sub):
            continue  # handled by evaluate_records
        kind = getattr(sub, "kind", "threshold")
        sensor_ids = [sub.sensor_id] if sub.sensor_id else list(latest_by_sensor)
        for sid in sensor_ids:
            if not sub.sensor_id and not _in_radius(sub, sid, sensor_coords):
                continue
            if kind == "trend":
                trend = trends.get((sid, sub.field))
                if trend is None or trend.direction != "rising" or (trend.pct_change or 0) < sub.threshold:
                    continue
                value, ts = trend.pct_change, now
            else:
                reading = latest_by_sensor.get(sid)
                if reading is None:
                    continue
                value = getattr(reading, sub.field, None)
                if value is None or value < sub.threshold:
                    continue
                ts = reading.ts
            alerts.append(Alert(
                subscription_id=sub.id, channel=sub.channel, target=sub.target,
                sensor_id=sid, field=sub.field, value=value,
                threshold=sub.threshold, ts=ts, kind=kind,
            ))
            break  # one alert per subscription per run (rate-limited as a unit)

    return alerts


def is_record_sub(sub) -> bool:
    """Spill / violation subscriptions. Older sign-ups stored these as
    ``kind="threshold"`` with ``field="spills"|"water"`` (the waitlist), so the field decides."""
    return getattr(sub, "kind", None) == "record" or getattr(sub, "field", None) in RECORD_RULES


def record_key(rec: dict) -> str:
    """What makes a record "the same" across runs: a status or contaminant change is new."""
    return f"{rec['site_id']}|{rec['kind']}|{rec.get('value') or ''}"


def nearest_county(lat: float, lon: float) -> str:
    """The county whose centroid is nearest — a stand-in for "the county this point is in"."""
    return min(WV_COUNTY_CENTROID, key=lambda c: _haversine_km(lat, lon, *WV_COUNTY_CENTROID[c]))


def _record_in_place(sub, rec: dict, home_county: str | None) -> bool:
    center = (getattr(sub, "center_lat", None), getattr(sub, "center_lon", None))
    radius = getattr(sub, "radius_km", None)
    if center[0] is None or center[1] is None or not radius:
        return True  # anywhere in WV
    if rec.get("lat") is not None and rec.get("lon") is not None \
            and _haversine_km(center[0], center[1], rec["lat"], rec["lon"]) <= radius:
        return True
    return bool(rec.get("county_placed") and home_county
                and canon_county(rec.get("county")) == home_county)


def _returned(sub, here, known, last_present) -> dict[str, datetime]:
    """Known records listed again after a real absence, with when each was last present.

    The gap is measured to the previous check, not to now, so downtime on our side doesn't
    read as a return. A row with no date (older than this tracking) is left alone.
    """
    if sub.field not in RETURNING_RULES:
        return {}
    gap = timedelta(days=RETURN_AFTER_DAYS)
    back = {}
    for r in here:
        k = record_key(r)
        last = last_present.get(k)
        if k in known and last is not None and sub.records_checked_at - last >= gap:
            back[k] = last
    return back


def evaluate_records(subscriptions, records, seen, now: datetime, tzname: str = EASTERN,
                     last_present=None):
    """Record alerts that should fire now, the baselines to store, and what's still listed.

    ``records`` are dicts with ``rule`` (a :data:`RECORD_RULES` key), ``site_id``, ``kind``,
    ``value``, ``lat``/``lon``/``county`` and display fields. ``seen`` maps a subscription
    id to the record keys it already knows. A subscription that has never been checked
    (``records_checked_at`` is None) doesn't fire: what's already on file for its place
    becomes its baseline, so only records that appear later alert. ``last_present`` maps a
    subscription id to ``{record key: when last present}``; a known record that returns
    after dropping off (:func:`_returned`) alerts again, carrying ``last_present`` (a date).

    Returns ``(alerts, baselines, present)``; ``baselines`` maps subscription id -> keys and
    ``present`` subscription id -> known keys to stamp as present (a checked subscription
    always has an entry). A returned record is left out until its alert goes out, so one
    held by quiet hours or the rate limit still counts as returned next run.
    """
    hour = _local_hour(now, tzname)
    alerts: list[Alert] = []
    baselines: dict[int, set[str]] = {}
    present: dict[int, set[str]] = {}
    for sub in subscriptions:
        if not sub.active or not is_record_sub(sub) or sub.field not in RECORD_RULES:
            continue
        lat, lon = getattr(sub, "center_lat", None), getattr(sub, "center_lon", None)
        home = nearest_county(lat, lon) if lat is not None and lon is not None else None
        here = [r for r in records if r["rule"] == sub.field and _record_in_place(sub, r, home)]
        if getattr(sub, "records_checked_at", None) is None:
            baselines[sub.id] = {record_key(r) for r in here}
            continue
        known = seen.get(sub.id, set())
        back = _returned(sub, here, known, (last_present or {}).get(sub.id, {}))
        present[sub.id] = ({record_key(r) for r in here} & set(known)) - set(back)
        if _in_quiet_hours(hour, sub.quiet_start, sub.quiet_end):
            continue  # held, not lost: still unseen next run
        if sub.last_notified_at is not None \
                and (now - sub.last_notified_at).total_seconds() < sub.min_interval_seconds:
            continue
        new, keys = [], set()
        for r in here:
            k = record_key(r)
            if k not in known and k not in keys:
                keys.add(k)
                new.append(r)
            elif k in back and k not in keys:
                keys.add(k)
                new.append({**r, "last_present": back[k].date().isoformat()})
        if not new:
            continue
        new.sort(key=lambda r: r.get("effective") or "", reverse=True)
        alerts.append(Alert(
            subscription_id=sub.id, channel=sub.channel, target=sub.target,
            sensor_id=new[0]["site_id"], field=sub.field, value=float(len(new)),
            threshold=0.0, ts=now, kind="record", label=getattr(sub, "label", None),
            records=new,
        ))
    return alerts, baselines, present


__all__ = ["Alert", "RECORD_RULES", "RETURNING_RULES", "RETURN_AFTER_DAYS", "evaluate", "evaluate_records", "is_record_sub",
           "nearest_county", "record_key"]
