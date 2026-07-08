"""Sensor health scoring.

Summarizes whether a sensor looks healthy from its recent readings:

- **offline** — hasn't reported within a freshness window (dropout)
- **degraded** — reporting, but a key channel is missing much/all of the time
  (e.g. the sensor we saw that never reports temperature/humidity)
- **ok** — reporting with its expected channels

This complements anomaly detection: a spike on a sensor that's also flagged
unhealthy is more likely a malfunction than a real event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_KEY_FIELDS = ("pm2_5", "temperature", "humidity")


@dataclass
class SensorHealth:
    sensor_id: str
    status: str  # ok | degraded | offline | no_data
    issues: list[str]
    reading_count: int
    last_ts: datetime | None
    null_fractions: dict[str, float]


def _naive(dt: datetime) -> datetime:
    """Drop tzinfo so aware/naive datetimes compare cleanly (all times are UTC)."""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def score_health(
    sensor_id: str,
    readings,
    now: datetime,
    offline_after: timedelta = timedelta(hours=6),
) -> SensorHealth:
    """Score one sensor's health from its recent readings."""
    count = len(readings)
    if count == 0:
        return SensorHealth(sensor_id, "no_data", ["no readings"], 0, None, {})

    now_n = _naive(now)
    last_ts = max(_naive(r.ts) for r in readings)
    issues: list[str] = []

    offline = now_n - last_ts > offline_after
    if offline:
        issues.append(f"no data for {now_n - last_ts}")

    null_fractions: dict[str, float] = {}
    for field in _KEY_FIELDS:
        nulls = sum(1 for r in readings if getattr(r, field) is None)
        frac = round(nulls / count, 2)
        null_fractions[field] = frac
        if frac == 1.0:
            issues.append(f"{field} never reported")
        elif frac > 0.5:
            issues.append(f"{field} missing {frac * 100:.0f}% of readings")

    if offline:
        status = "offline"
    elif issues:
        status = "degraded"
    else:
        status = "ok"

    return SensorHealth(sensor_id, status, issues, count, last_ts, null_fractions)
