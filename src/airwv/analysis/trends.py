"""Long-term trend detection.

Fits a linear trend to a sensor's daily-median series for a field, to answer "is
this getting worse?". Daily medians (not raw readings) are used so a few spikes
or the diurnal cycle don't dominate the slope. A trend is only called rising or
falling when the correlation is strong enough to be more than noise.

This feeds "areas to watch": sites whose pollutant is trending up over months are
flagged for closer attention.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

# Pollutants where "rising" is bad (used to decide what to flag as worsening).
POLLUTANTS = {"pm2_5", "pm1_0", "pm10", "voc", "aqi"}

# Minimum |correlation| for a slope to count as a real direction, not noise.
_MIN_R = 0.3


@dataclass
class Trend:
    field: str
    n_days: int
    slope_per_30d: float | None  # change in field units per 30 days
    first: float | None          # fitted value at series start
    last: float | None           # fitted value at series end
    pct_change: float | None     # total fitted change as % of start
    direction: str               # "rising" | "falling" | "flat" | "insufficient"
    r: float | None              # correlation strength


def daily_medians(readings, field: str) -> list[tuple]:
    buckets: dict = defaultdict(list)
    for r in readings:
        value = getattr(r, field)
        if value is not None:
            buckets[r.ts.date()].append(value)
    return sorted((day, statistics.median(vals)) for day, vals in buckets.items())


def linear_trend(readings, field: str = "pm2_5", min_days: int = 14) -> Trend:
    """Fit a linear trend to the daily-median series of ``field``."""
    days = daily_medians(readings, field)
    if len(days) < min_days:
        return Trend(field, len(days), None, None, None, None, "insufficient", None)

    x0 = days[0][0].toordinal()
    xs = [d.toordinal() - x0 for d, _ in days]
    ys = [v for _, v in days]

    slope, intercept = statistics.linear_regression(xs, ys)
    try:
        r = statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        r = 0.0

    first = intercept
    last = intercept + slope * xs[-1]
    pct = round((last - first) / first * 100, 1) if first else None

    if abs(r) < _MIN_R:
        direction = "flat"
    else:
        direction = "rising" if slope > 0 else "falling"

    return Trend(
        field=field,
        n_days=len(days),
        slope_per_30d=round(slope * 30, 3),
        first=round(first, 1),
        last=round(last, 1),
        pct_change=pct,
        direction=direction,
        r=round(r, 2),
    )


def is_worsening(trend: Trend, min_pct: float = 15.0) -> bool:
    """A pollutant trending up by at least ``min_pct`` over the period → watch it."""
    return (
        trend.field in POLLUTANTS
        and trend.direction == "rising"
        and (trend.pct_change or 0) >= min_pct
    )
