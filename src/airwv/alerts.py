"""Evaluate subscriptions against the latest readings.

Pure logic (no I/O): given active subscriptions and the newest reading per
sensor, decide which should fire — respecting the threshold, per-subscription
rate limiting, and local quiet hours. Delivery is handled separately by the
notify channels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

EASTERN = "America/New_York"


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
    kind: str = "threshold"  # "threshold" | "trend"


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


__all__ = ["Alert", "evaluate"]
