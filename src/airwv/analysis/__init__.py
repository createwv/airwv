"""Analysis: anomaly detection, sensor health, and (later) trends."""

from airwv.analysis.anomaly import Anomaly, detect_spikes, detect_stuck
from airwv.analysis.health import SensorHealth, score_health

__all__ = ["Anomaly", "SensorHealth", "detect_spikes", "detect_stuck", "score_health"]
