"""Serialize stored readings to CSV/JSON for sharing, archival, or analysis."""

from __future__ import annotations

import csv
import io

# Column order for exports (mirrors the readings schema, minus internals).
COLUMNS = [
    "ts", "source", "sensor_id", "lat", "lon",
    "pm1_0", "pm2_5", "pm10", "aqi", "voc",
    "temperature", "humidity", "pressure",
    "pm2_5_a", "pm2_5_b", "confidence",
    "count_0_3", "count_0_5", "count_1_0", "count_2_5", "count_5_0", "count_10_0",
    "quality",
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


# /api/query?format=csv: one row per site (``show=sites``) or per record (``show=records``).
QUERY_SITE_COLUMNS = [
    "site_id", "kind", "medium", "layer", "ref", "name", "lat", "lon", "county", "mi",
    "parameter", "value", "unit", "ts", "peak_value", "peak_ts", "records", "url",
]
QUERY_RECORD_COLUMNS = [
    "site_id", "site_kind", "name", "medium", "mi", "record_kind", "value", "effective",
    "as_of", "url",
]


def _csv(columns, rows) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def query_sites_to_csv(sites) -> str:
    rows = []
    for s in sites:
        latest, peak = s.get("latest") or {}, s.get("peak") or {}
        rows.append({**s, "parameter": latest.get("parameter"), "value": latest.get("value"),
                     "unit": latest.get("unit"), "ts": latest.get("ts"),
                     "peak_value": peak.get("value"), "peak_ts": peak.get("ts"),
                     "records": len(s.get("records") or [])})
    return _csv(QUERY_SITE_COLUMNS, rows)


def query_records_to_csv(records) -> str:
    return _csv(QUERY_RECORD_COLUMNS, [{**r, "record_kind": r["kind"]} for r in records])
