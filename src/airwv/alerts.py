"""Evaluate subscriptions against the latest readings.

Pure logic (no I/O): given active subscriptions and the newest reading per
sensor, decide which should fire — respecting the threshold, per-subscription
rate limiting, and local quiet hours. Delivery is handled separately by the
notify channels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = "America/New_York"


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


def evaluate(subscriptions, latest_by_sensor, now: datetime, tzname: str = EASTERN,
             trends=None) -> list[Alert]:
    """Return the alerts that should fire right now.

    ``trends`` maps ``(sensor_id, field) -> Trend`` and is only needed for
    trend-kind subscriptions (fire when a field is rising by >= threshold percent).
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
