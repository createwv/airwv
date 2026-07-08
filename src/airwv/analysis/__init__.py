"""Analysis: anomaly detection, sensor health, and time-of-day patterns."""

from airwv.analysis.anomaly import Anomaly, detect_spikes, detect_stuck
from airwv.analysis.events import Event, detrend_events
from airwv.analysis.health import SensorHealth, score_health
from airwv.analysis.patterns import (
    HourStat,
    diurnal_amplitude,
    hour_of_day_profile,
    part_of_day_summary,
)
from airwv.analysis.trends import Trend, is_worsening, linear_trend

__all__ = [
    "Anomaly",
    "SensorHealth",
    "HourStat",
    "Event",
    "detect_spikes",
    "detect_stuck",
    "detrend_events",
    "score_health",
    "hour_of_day_profile",
    "part_of_day_summary",
    "diurnal_amplitude",
    "Trend",
    "linear_trend",
    "is_worsening",
]
