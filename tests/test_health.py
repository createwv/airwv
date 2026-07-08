"""Tests for sensor health scoring."""

from datetime import datetime, timedelta, timezone

from airwv.analysis.health import score_health
from airwv.sources.base import Reading

_NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def _reading(hours_ago, **kw):
    base = dict(source="purpleair", sensor_id="1", ts=_NOW - timedelta(hours=hours_ago), pm2_5=10.0)
    base.update(kw)
    return Reading(**base)


def test_no_data():
    h = score_health("1", [], _NOW)
    assert h.status == "no_data"


def test_healthy_sensor_is_ok():
    readings = [_reading(i, temperature=70, humidity=50) for i in range(3)]
    h = score_health("1", readings, _NOW)
    assert h.status == "ok"
    assert h.issues == []


def test_offline_when_stale():
    readings = [_reading(48, temperature=70, humidity=50)]  # 2 days old
    h = score_health("1", readings, _NOW)
    assert h.status == "offline"


def test_missing_channel_is_degraded():
    # Reports PM but never temperature/humidity (like sensor 196533).
    readings = [_reading(i, temperature=None, humidity=None) for i in range(3)]
    h = score_health("1", readings, _NOW)
    assert h.status == "degraded"
    assert h.null_fractions["temperature"] == 1.0
    assert any("temperature never reported" in i for i in h.issues)
