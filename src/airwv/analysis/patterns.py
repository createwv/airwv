"""Time-of-day / day-of-week pattern analysis.

Buckets a sensor's readings by **local** hour-of-day (and weekday) so recurring
diurnal patterns surface — e.g. whether a pollutant runs higher in the evening
than during the day at a given site. Stored timestamps are UTC; we convert to a
local timezone (default US Eastern, with automatic EST/EDT handling) because
"evening" only means anything in local time.

This is descriptive, not causal: a pattern here is a prompt to investigate
(corroborate with neighbors, wind, and known sources), not a conclusion.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import timezone
from zoneinfo import ZoneInfo

EASTERN = "America/New_York"


@dataclass
class HourStat:
    hour: int  # 0-23, local time
    count: int
    mean: float | None
    median: float | None


def _local_hour(ts, tzname: str) -> int:
    aware = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(tzname)).hour


def hour_of_day_profile(readings, field: str = "voc", tzname: str = EASTERN) -> list[HourStat]:
    """Per-local-hour count/mean/median of ``field`` (nulls ignored)."""
    buckets: dict[int, list[float]] = {h: [] for h in range(24)}
    for r in readings:
        value = getattr(r, field)
        if value is not None:
            buckets[_local_hour(r.ts, tzname)].append(value)

    profile: list[HourStat] = []
    for hour in range(24):
        vals = buckets[hour]
        if vals:
            profile.append(HourStat(hour, len(vals), round(statistics.mean(vals), 1),
                                    round(statistics.median(vals), 1)))
        else:
            profile.append(HourStat(hour, 0, None, None))
    return profile


def _median_over(profile: list[HourStat], hours) -> float | None:
    vals = [s.median for s in profile if s.hour in hours and s.median is not None]
    return round(statistics.mean(vals), 1) if vals else None


def part_of_day_summary(profile: list[HourStat]) -> dict[str, float | None]:
    """Compare typical values across business / evening / overnight hours."""
    business = _median_over(profile, range(9, 17))      # 9a–5p
    evening = _median_over(profile, range(18, 24))       # 6p–midnight
    overnight = _median_over(profile, range(0, 6))       # midnight–6a
    ratio = round(evening / business, 2) if evening and business else None
    return {
        "business_9_17": business,
        "evening_18_23": evening,
        "overnight_0_5": overnight,
        "evening_vs_business_ratio": ratio,
    }
