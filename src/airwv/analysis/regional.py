"""Regional / network baseline context.

Answers "is this site worse than typical for the network?" by comparing each
sensor's median to a baseline (the median across sensors). A site persistently
above the network baseline is elevated relative to its peers — useful context
that a single sensor's numbers can't provide.

Meaningful in proportion to how many sensors have data; thin with a handful,
strong once the fleet is backfilled.
"""

from __future__ import annotations

import statistics


def sensor_medians(readings_by_sensor: dict, field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for sensor_id, readings in readings_by_sensor.items():
        vals = [getattr(r, field) for r in readings if getattr(r, field) is not None]
        if vals:
            out[sensor_id] = statistics.median(vals)
    return out


def compare_to_baseline(medians: dict[str, float]) -> dict:
    """Given each sensor's median, return the network baseline + per-sensor deviation."""
    if not medians:
        return {"baseline": None, "sensors": {}}

    baseline = statistics.median(medians.values())
    sensors = {
        sid: {
            "median": round(m, 1),
            "delta": round(m - baseline, 1),
            "pct_vs_baseline": round((m - baseline) / baseline * 100, 1) if baseline else None,
        }
        for sid, m in medians.items()
    }
    return {"baseline": round(baseline, 1), "sensors": sensors}
