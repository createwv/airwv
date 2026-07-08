"""Episodic event detection by de-trending the daily cycle.

A sensor's routine diurnal swing (valley inversion, traffic, etc.) can hide
discrete pollution *events*. We remove the typical value for each local hour
(the baseline), then flag readings whose *residual* is a strong robust-z outlier.
What's left after de-trending is the episodic signal — spikes that aren't just
"the usual evening buildup".

Pairs with neighbor cross-checks: an event that shows only at one sensor is
either a truly local release or a sensor glitch; one shared with neighbors is a
real area-wide event.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime

from airwv.analysis.patterns import EASTERN, _local_hour

_MAD_TO_SIGMA = 0.6745


@dataclass
class Event:
    sensor_id: str
    ts: datetime  # as stored (UTC)
    field: str
    value: float
    baseline: float
    residual: float
    score: float  # robust z of the residual


def detrend_events(
    readings,
    field: str = "pm2_5",
    tzname: str = EASTERN,
    z_threshold: float = 6.0,
    min_points: int = 48,
) -> list[Event]:
    """Flag readings whose de-trended residual is a strong outlier, worst first."""
    series = [(r.ts, getattr(r, field)) for r in readings if getattr(r, field) is not None]
    if len(series) < min_points:
        return []

    by_hour: dict[int, list[float]] = {h: [] for h in range(24)}
    for ts, value in series:
        by_hour[_local_hour(ts, tzname)].append(value)
    baseline = {h: statistics.median(vals) for h, vals in by_hour.items() if vals}

    residuals = []
    for ts, value in series:
        base = baseline.get(_local_hour(ts, tzname))
        if base is not None:
            residuals.append((ts, value, base, value - base))

    diffs = [d for _, _, _, d in residuals]
    med = statistics.median(diffs)
    mad = statistics.median([abs(d - med) for d in diffs]) or 1.0

    sensor_id = readings[0].sensor_id
    events = [
        Event(sensor_id, ts, field, value, round(base, 1), round(diff, 1),
              round(_MAD_TO_SIGMA * (diff - med) / mad, 1))
        for ts, value, base, diff in residuals
        if _MAD_TO_SIGMA * (diff - med) / mad >= z_threshold
    ]
    events.sort(key=lambda e: -e.score)
    return events
