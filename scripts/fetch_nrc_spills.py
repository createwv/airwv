#!/usr/bin/env python3
"""Pull WV spill/release reports from the National Response Center (NRC).

The NRC (run by the U.S. Coast Guard) is the federal point of contact for reporting
oil, chemical, and other releases — the closest thing to a public, machine-readable
record of "a spill was reported here." This answers "where does spill data go?": the
initial report lands here, even when a state agency's follow-up samples never become
public.

NRC publishes one Excel workbook per calendar year (relational sheets keyed on
SEQNOS). We download the recent years, keep WV incidents, and flatten
INCIDENT_COMMONS + MATERIAL_INVOLVED + INCIDENT_DETAILS + CALLS into one record per
report. ~80 WV reports/year; ~half reach water. Reports without coordinates (most)
are placed at their county centroid and flagged as approximate.

Requires openpyxl (dev dependency). Runs locally; output JSON is committed and
served, so the server never needs openpyxl.

    python scripts/fetch_nrc_spills.py                 # last 2 calendar years
    python scripts/fetch_nrc_spills.py --years 2024,2025,2026

Writes src/airwv/data/nrc_spills.json, served by /api/nrc-spills and shown on the
Events page (map layer + list) as reported spills.
"""
from __future__ import annotations

import argparse
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import openpyxl

from airwv.wvgeo import county_centroid

URL = "https://nrc.uscg.mil/FOIAFiles/CY{yy}.xlsx"
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "nrc_spills.json"


def _sheet(wb, name: str) -> list[dict]:
    ws = wb[name]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    return [dict(zip(hdr, r)) for r in it]


def _dms(deg, minute, sec, quad) -> float | None:
    if deg in (None, "", 0):
        return None
    try:
        v = float(deg) + float(minute or 0) / 60 + float(sec or 0) / 3600
    except (TypeError, ValueError):
        return None
    return -v if str(quad).upper() in ("S", "W") else v


def _date(v) -> str | None:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if v:
        try:
            return datetime.strptime(str(v).split()[0], "%m/%d/%Y").date().isoformat()
        except ValueError:
            return None
    return None


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def fetch_year(client: httpx.Client, year: int) -> list[dict]:
    r = client.get(URL.format(yy=f"{year % 100:02d}"), timeout=180,
                   headers={"User-Agent": "Mozilla/5.0 (AirWV data fetch)"})
    r.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    commons = [r for r in _sheet(wb, "INCIDENT_COMMONS")
               if (r.get("LOCATION_STATE") or "").strip().upper() == "WV"]
    wv_ids = {r["SEQNOS"] for r in commons}
    mats = defaultdict(list)
    for m in _sheet(wb, "MATERIAL_INVOLVED"):
        if m["SEQNOS"] in wv_ids:
            mats[m["SEQNOS"]].append(m)
    details = {d["SEQNOS"]: d for d in _sheet(wb, "INCIDENT_DETAILS") if d["SEQNOS"] in wv_ids}
    calls = {c["SEQNOS"]: c for c in _sheet(wb, "CALLS") if c["SEQNOS"] in wv_ids}

    out = []
    for r in commons:
        sid = r["SEQNOS"]
        d = details.get(sid, {})
        ms = mats.get(sid, [])
        materials = [{
            "name": (m.get("NAME_OF_MATERIAL") or "").strip().title() or "Unknown material",
            "amount": _num(m.get("AMOUNT_OF_MATERIAL")),
            "unit": (m.get("UNIT_OF_MEASURE") or "").strip().lower() or None,
            "reached_water": (m.get("IF_REACHED_WATER") or "").strip().upper() == "YES",
        } for m in ms]
        reached = any(m["reached_water"] for m in materials) or bool(d.get("BODY_OF_WATER"))
        lat = _dms(r.get("LAT_DEG"), r.get("LAT_MIN"), r.get("LAT_SEC"), r.get("LAT_QUAD"))
        lon = _dms(r.get("LONG_DEG"), r.get("LONG_MIN"), r.get("LONG_SEC"), r.get("LONG_QUAD"))
        county = (r.get("LOCATION_COUNTY") or "").strip().title() or None
        geo = "exact"
        if lat is None or lon is None:
            cc = county_centroid(county)
            if cc is None:
                continue                       # no coords and no known county → skip
            lat, lon, geo = cc[0], cc[1], "county"
        out.append({
            "report_id": sid,
            "date": _date(r.get("INCIDENT_DATE_TIME")),
            "city": (r.get("LOCATION_NEAREST_CITY") or "").strip().title() or None,
            "county": county,
            "type": (r.get("TYPE_OF_INCIDENT") or "").strip().title() or None,
            "cause": (r.get("INCIDENT_CAUSE") or "").strip().title() or None,
            "company": (calls.get(sid, {}).get("RESPONSIBLE_COMPANY") or "").strip().title() or None,
            "materials": materials,
            "reached_water": reached,
            "body_of_water": (d.get("BODY_OF_WATER") or "").strip().title() or None,
            "water_supply_contaminated": (d.get("WATER_SUPPLY_CONTAMINATED") or "").strip().upper() == "YES",
            "description": " ".join((r.get("DESCRIPTION_OF_INCIDENT") or "").split())[:500] or None,
            "lat": round(lat, 5), "lon": round(lon, 5), "geo": geo,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", help="comma-separated calendar years (default: last 2)")
    ap.add_argument("--this-year", type=int, default=2026, help="latest year to default from")
    args = ap.parse_args()
    years = ([int(y) for y in args.years.split(",")] if args.years
             else [args.this_year - 1, args.this_year])
    spills = []
    with httpx.Client(follow_redirects=True) as client:
        for y in years:
            try:
                rows = fetch_year(client, y)
            except Exception as exc:
                print(f"  CY{y}: failed ({exc})")
                continue
            print(f"  CY{y}: {len(rows)} WV reports ({sum(s['reached_water'] for s in rows)} reached water)")
            spills.extend(rows)
    spills.sort(key=lambda s: s["date"] or "", reverse=True)
    data = {
        "source": "National Response Center (US Coast Guard) — reported oil/chemical releases",
        "scope": f"WV reports for calendar years {years}",
        "disclaimer": ("These are INITIAL reports to the NRC — unverified, as-reported, and not a "
                       "determination of impact. The NRC is a call center, not a response agency. "
                       "Most reports lack precise coordinates; those are placed at the county "
                       "centroid (marked approximate)."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "spills": spills,
    }
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    exact = sum(1 for s in spills if s["geo"] == "exact")
    print(f"wrote {len(spills)} spills → {OUT}  ({exact} exact coords, {len(spills) - exact} county-placed)")


if __name__ == "__main__":
    main()
