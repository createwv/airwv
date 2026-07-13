"""Pull discrete WV water-quality samples from the EPA/USGS **Water Quality Portal**
(waterqualitydata.us — keyless) into the ``water_readings`` table (source='wqp').

Complements the live USGS gauges with lab/grab-sample data that gauges don't carry —
bacteria (E. coli), metals (iron, aluminum, manganese), sulfate, nitrate, TDS — across
far more sites (hundreds). Discrete/episodic, so it's the *history* side of water, the
way EPA AirData is for air. Idempotent via the (source, site_id, ts, parameter) key.

    python scripts/fetch_water_samples.py --start-year 2020
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import os

import httpx

from airwv.storage import Store

WQP = "https://www.waterqualitydata.us/data/Result/search"
# WQP CharacteristicName -> (our parameter key, canonical unit)
CHARS = {
    "pH": ("ph", "std units"),
    "Specific conductance": ("conductance", "µS/cm"),
    "Dissolved oxygen (DO)": ("do", "mg/L"),
    "Turbidity": ("turbidity", "NTU"),
    "Temperature, water": ("temperature", "°C"),
    "Escherichia coli": ("ecoli", "MPN/100mL"),
    "Iron": ("iron", "mg/L"),
    "Sulfate": ("sulfate", "mg/L"),
    "Nitrate": ("nitrate", "mg/L"),
    "Total dissolved solids": ("tds", "mg/L"),
    "Aluminum": ("aluminum", "mg/L"),
    "Manganese": ("manganese", "mg/L"),
    "Selenium": ("selenium", "µg/L"),
}
_MG_METALS = {"iron", "aluminum", "manganese"}   # stored mg/L; convert µg→mg
_UG_METALS = {"selenium"}                        # stored µg/L (values ~1–20); convert mg→µg


def _num(v: str):
    if not v:
        return None
    v = v.strip().lstrip("<>").strip()
    try:
        return float(v)
    except ValueError:
        return None


def fetch_char(char: str, start: str) -> list[dict]:
    param, unit = CHARS[char]
    p = {"statecode": "US:54", "characteristicName": char, "startDateLo": start,
         "mimeType": "csv", "dataProfile": "resultPhysChem"}
    r = httpx.get(WQP, params=p, timeout=300)
    rows = []
    for row in csv.DictReader(io.StringIO(r.text)):
        val = _num(row.get("ResultMeasureValue"))
        if val is None:
            continue
        try:
            lat = float(row["ActivityLocation/LatitudeMeasure"])
            lon = float(row["ActivityLocation/LongitudeMeasure"])
            ts = _dt.datetime.strptime(row["ActivityStartDate"], "%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        u = (row.get("ResultMeasure/MeasureUnitCode") or "").lower()
        if param in _MG_METALS and u.startswith("ug"):   # µg/L -> mg/L
            val /= 1000.0
        if param in _UG_METALS and u.startswith("mg"):   # mg/L -> µg/L
            val *= 1000.0
        rows.append({"source": "wqp", "site_id": row["MonitoringLocationIdentifier"],
                     "site_name": (row.get("MonitoringLocationName") or row["MonitoringLocationIdentifier"])[:200],
                     "lat": round(lat, 5), "lon": round(lon, 5), "ts": ts,
                     "parameter": param, "value": round(val, 4), "unit": unit})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-year", type=int, default=2020)
    ap.add_argument("--chars", help="comma-separated CharacteristicNames to fetch "
                                     "(default: all). e.g. --chars Selenium")
    args = ap.parse_args()
    start = f"01-01-{args.start_year}"
    chars = [c.strip() for c in args.chars.split(",")] if args.chars else list(CHARS)
    unknown = [c for c in chars if c not in CHARS]
    if unknown:
        ap.error(f"unknown characteristic(s): {unknown}. Known: {list(CHARS)}")
    store = Store(os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite")
    store.create_schema()
    total = 0
    for char in chars:
        try:
            rows = fetch_char(char, start)
        except Exception as exc:
            print(f"  {char}: fetch failed ({exc})")
            continue
        store.add_water_readings(rows)
        total += len(rows)
        sites = len({r["site_id"] for r in rows})
        print(f"  {char:22} {len(rows):6} rows  {sites:4} sites")
    print(f"WQP: {total} samples stored (source=wqp, since {start})")


if __name__ == "__main__":
    main()
