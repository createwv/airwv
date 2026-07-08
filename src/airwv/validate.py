"""Write-time sanity guards for readings.

We never drop raw data (see ARCHITECTURE.md). Instead, readings with physically
implausible values are *flagged* — the storage layer marks them ``quality =
"suspect"`` so downstream analysis can filter them while the raw record is kept.

These are coarse physical-plausibility bounds, not anomaly detection (that's
Phase 2, which compares a reading against a sensor's own history and neighbors).
Bounds are deliberately generous so genuine extreme events (e.g. wildfire smoke)
pass, and only impossible/broken values are caught.
"""

from __future__ import annotations

from airwv.sources.base import Reading

# field name -> (inclusive low, inclusive high)
_RANGES: dict[str, tuple[float, float]] = {
    "pm1_0": (0.0, 5000.0),
    "pm2_5": (0.0, 5000.0),
    "pm10": (0.0, 5000.0),
    "aqi": (0.0, 1000.0),
    "voc": (0.0, 60000.0),
    "humidity": (0.0, 100.0),
    "pressure": (300.0, 1100.0),  # hPa; wide enough for high-altitude stations
    "temperature": (-60.0, 160.0),  # PurpleAir reports °F
}


def validate_reading(reading: Reading) -> list[str]:
    """Return a list of range issues for a reading (empty means it looks fine)."""
    issues: list[str] = []
    for field, (low, high) in _RANGES.items():
        value = getattr(reading, field)
        if value is None:
            continue
        if value < low or value > high:
            issues.append(f"{field}={value} out of range [{low}, {high}]")
    return issues


def is_suspect(reading: Reading) -> bool:
    return bool(validate_reading(reading))
