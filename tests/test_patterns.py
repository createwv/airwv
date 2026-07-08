"""Tests for time-of-day pattern analysis."""

from datetime import datetime, timezone

from airwv.analysis.patterns import (
    _local_hour,
    hour_of_day_profile,
    part_of_day_summary,
)
from airwv.sources.base import Reading


def _reading(ts, voc):
    return Reading(source="purpleair", sensor_id="1", ts=ts, voc=voc)


def test_local_hour_converts_utc_to_eastern():
    # 2024-07-01 22:00 UTC is 18:00 EDT (UTC-4).
    ts = datetime(2024, 7, 1, 22, 0, tzinfo=timezone.utc)
    assert _local_hour(ts, "America/New_York") == 18


def test_profile_buckets_by_hour_in_utc():
    readings = [
        _reading(datetime(2024, 6, 1, 20, tzinfo=timezone.utc), 200),
        _reading(datetime(2024, 6, 2, 20, tzinfo=timezone.utc), 220),
        _reading(datetime(2024, 6, 1, 10, tzinfo=timezone.utc), 100),
    ]
    profile = hour_of_day_profile(readings, field="voc", tzname="UTC")
    by_hour = {s.hour: s for s in profile}
    assert by_hour[20].count == 2
    assert by_hour[20].median == 210
    assert by_hour[10].median == 100
    assert by_hour[3].count == 0  # empty hour present with None


def test_part_of_day_summary_flags_evening_elevation():
    readings = []
    # Evening (20:00 UTC) high, business hours (14:00 UTC) low.
    for day in range(1, 6):
        readings.append(_reading(datetime(2024, 6, day, 20, tzinfo=timezone.utc), 300))
        readings.append(_reading(datetime(2024, 6, day, 14, tzinfo=timezone.utc), 100))
    profile = hour_of_day_profile(readings, field="voc", tzname="UTC")
    summary = part_of_day_summary(profile)
    assert summary["evening_18_23"] > summary["business_9_17"]
    assert summary["evening_vs_business_ratio"] > 1
