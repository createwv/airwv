"""Tests for de-trended episodic event detection."""

from datetime import datetime, timedelta, timezone

from airwv.analysis.events import detrend_events
from airwv.sources.base import Reading

_T0 = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _series(values):
    return [
        Reading(source="purpleair", sensor_id="1", ts=_T0 + timedelta(hours=i), pm2_5=v)
        for i, v in enumerate(values)
    ]


def test_finds_spike_above_diurnal_baseline():
    # Two flat weeks of ~10, then a single 800 spike.
    values = [10] * 200
    values[150] = 800.0
    events = detrend_events(_series(values), field="pm2_5", tzname="UTC", z_threshold=6)
    assert events
    assert events[0].value == 800.0
    assert events[0].residual > 700


def test_flat_series_has_no_events():
    events = detrend_events(_series([10] * 200), field="pm2_5", tzname="UTC", z_threshold=6)
    assert events == []


def test_too_few_points_returns_nothing():
    assert detrend_events(_series([10, 800]), field="pm2_5") == []
