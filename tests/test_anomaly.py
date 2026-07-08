"""Tests for spike and stuck-value anomaly detection."""

from datetime import datetime, timedelta, timezone

from airwv.analysis.anomaly import detect_spikes, detect_stuck
from airwv.sources.base import Reading

_T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _readings(values, sensor_id="1"):
    return [
        Reading(source="purpleair", sensor_id=sensor_id, ts=_T0 + timedelta(hours=i), pm2_5=v)
        for i, v in enumerate(values)
    ]


def test_spike_is_detected():
    # Steady ~10 with one wild 1678 spike (like the real reading we saw).
    values = [10, 11, 9, 10, 12, 8, 11, 10, 9, 11, 10, 12, 1678.6, 10, 9]
    anomalies = detect_spikes(_readings(values))
    assert any(a.value == 1678.6 and a.kind == "spike" for a in anomalies)


def test_normal_variation_not_flagged():
    values = [10, 11, 9, 10, 12, 8, 11, 10, 9, 11, 10, 12, 10, 9, 11]
    assert detect_spikes(_readings(values)) == []


def test_too_few_points_returns_nothing():
    assert detect_spikes(_readings([10, 1000])) == []


def test_stuck_values_detected():
    values = [10, 10, 10, 10, 10, 10, 10, 10]  # frozen channel
    anomalies = detect_stuck(_readings(values), run_length=6)
    assert anomalies
    assert anomalies[0].kind == "stuck"


def test_varying_values_not_stuck():
    values = [10, 11, 12, 13, 14, 15, 16]
    assert detect_stuck(_readings(values), run_length=6) == []
