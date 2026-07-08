"""Anomaly detection over a sensor's stored readings.

Two complementary checks, both operating on one sensor's time series:

- **Spikes** — values that deviate sharply from the sensor's own recent history,
  using a *robust* z-score (median + MAD). Robust stats matter here: a mean/stdev
  approach is itself skewed by the very spikes we're hunting, whereas the median
  barely moves. This is what separates a genuine implausible jump (e.g. the
  1678 µg/m³ reading we saw) from normal variation.
- **Stuck values** — the same value repeated many times in a row, a classic
  signature of a frozen/failed sensor channel.

These flag candidates for attention; they don't decide "real event vs.
malfunction" on their own — that's corroborated with neighbor sensors and health
signals. Functions take any objects exposing ``ts``, ``sensor_id``, and the field
(e.g. stored rows or Reading objects), so they're testable without a database.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime

_MAD_TO_SIGMA = 0.6745  # scales MAD to be comparable to a standard deviation


@dataclass
class Anomaly:
    sensor_id: str
    ts: datetime
    field: str
    value: float
    score: float
    kind: str  # "spike" | "stuck"
    detail: str


def _series(readings, field: str) -> list[tuple[datetime, float]]:
    return [(r.ts, getattr(r, field)) for r in readings if getattr(r, field) is not None]


def detect_spikes(
    readings,
    field: str = "pm2_5",
    threshold: float = 3.5,
    min_points: int = 12,
) -> list[Anomaly]:
    """Flag readings whose robust z-score exceeds ``threshold``."""
    series = _series(readings, field)
    if len(series) < min_points:
        return []

    values = [v for _, v in series]
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    if mad == 0:  # no spread — stuck detection handles this case
        return []

    sensor_id = readings[0].sensor_id
    anomalies: list[Anomaly] = []
    for ts, value in series:
        z = _MAD_TO_SIGMA * (value - median) / mad
        if abs(z) >= threshold:
            anomalies.append(
                Anomaly(
                    sensor_id=sensor_id,
                    ts=ts,
                    field=field,
                    value=value,
                    score=round(z, 2),
                    kind="spike",
                    detail=f"robust z={z:.1f} vs median {median:.1f}",
                )
            )
    return anomalies


def detect_stuck(
    readings,
    field: str = "pm2_5",
    run_length: int = 6,
) -> list[Anomaly]:
    """Flag the point where a value has repeated ``run_length`` times in a row."""
    series = _series(readings, field)
    if not series:
        return []

    sensor_id = readings[0].sensor_id
    anomalies: list[Anomaly] = []
    run = 1
    for i in range(1, len(series)):
        if series[i][1] == series[i - 1][1]:
            run += 1
        else:
            run = 1
        if run == run_length:
            anomalies.append(
                Anomaly(
                    sensor_id=sensor_id,
                    ts=series[i][0],
                    field=field,
                    value=series[i][1],
                    score=float(run),
                    kind="stuck",
                    detail=f"{run_length}+ identical values in a row",
                )
            )
    return anomalies
