"""Tests for long-term trend detection."""

from datetime import datetime, timedelta, timezone

from airwv.analysis.trends import is_worsening, linear_trend
from airwv.sources.base import Reading

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _daily(values):
    # one reading per day
    return [
        Reading(source="purpleair", sensor_id="1", ts=_T0 + timedelta(days=i), pm2_5=float(v))
        for i, v in enumerate(values)
    ]


def test_rising_trend_detected_and_flagged():
    trend = linear_trend(_daily(range(10, 40)), field="pm2_5")  # 10 -> 39 over 30 days
    assert trend.direction == "rising"
    assert trend.pct_change > 100
    assert is_worsening(trend)


def test_falling_trend():
    trend = linear_trend(_daily(range(40, 10, -1)), field="pm2_5")
    assert trend.direction == "falling"
    assert not is_worsening(trend)


def test_flat_trend_not_flagged():
    trend = linear_trend(_daily([20] * 30), field="pm2_5")
    assert trend.direction == "flat"
    assert not is_worsening(trend)


def test_insufficient_data():
    trend = linear_trend(_daily([10, 20, 30]), field="pm2_5", min_days=14)
    assert trend.direction == "insufficient"
    assert not is_worsening(trend)
