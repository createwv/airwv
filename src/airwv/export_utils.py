"""Serialize stored readings to CSV/JSON for sharing, archival, or analysis."""

from __future__ import annotations

import csv
import io

# Column order for exports (mirrors the readings schema, minus internals).
COLUMNS = [
    "ts", "source", "sensor_id", "lat", "lon",
    "pm1_0", "pm2_5", "pm10", "aqi", "voc",
    "temperature", "humidity", "pressure", "quality",
]


def _value(row, col):
    v = getattr(row, col)
    if col == "ts" and v is not None:
        return v.isoformat()
    return v


def readings_to_records(rows) -> list[dict]:
    return [{col: _value(r, col) for col in COLUMNS} for r in rows]


def readings_to_csv(rows) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([_value(r, col) for col in COLUMNS])
    return buf.getvalue()
