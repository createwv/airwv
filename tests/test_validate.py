"""Tests for write-time range guards."""

from datetime import datetime, timezone

from airwv.sources.base import Reading
from airwv.validate import is_suspect, validate_reading


def _reading(**kwargs) -> Reading:
    base = dict(source="purpleair", sensor_id="1", ts=datetime(2026, 7, 1, tzinfo=timezone.utc))
    base.update(kwargs)
    return Reading(**base)


def test_in_range_reading_is_clean():
    r = _reading(pm2_5=12.5, humidity=45, temperature=72, pressure=1013)
    assert validate_reading(r) == []
    assert not is_suspect(r)


def test_negative_pm_is_flagged():
    assert is_suspect(_reading(pm2_5=-3.0))


def test_humidity_over_100_is_flagged():
    issues = validate_reading(_reading(humidity=150))
    assert any("humidity" in i for i in issues)


def test_missing_values_are_ignored():
    assert validate_reading(_reading()) == []


def test_extreme_but_possible_smoke_passes():
    # Wildfire smoke can drive PM2.5 very high — must not be flagged.
    assert not is_suspect(_reading(pm2_5=900.0))
