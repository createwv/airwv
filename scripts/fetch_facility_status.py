#!/usr/bin/env python3
"""Pull WV major regulated facilities + their compliance status from EPA ECHO.

ECHO (Enforcement & Compliance History Online) is EPA's public record of every
regulated facility's permit + compliance status — keyless, authoritative. We pull
the state's "major" facilities (big NPDES water dischargers + major air sources),
each with its current compliance status, significant-non-compliance flag, program
mix, and a link to its Detailed Facility Report.

Two calls are joined on RegistryID because ECHO splits the fields across services:
  - get_qid (JSON)     → compliance fields + FacLat (but no longitude)
  - get_download (CSV) → RegistryID + FacLong (but not the compliance fields)

Writes src/airwv/data/echo_facilities.json, served by /api/facilities and shown
on the Sources page as the compliance/permit layer.

    python scripts/fetch_facility_status.py            # WV majors (default)
    python scripts/fetch_facility_status.py --state OH
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "https://echodata.epa.gov/echo/echo_rest_services"
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "echo_facilities.json"


def _get(client: httpx.Client, path: str, **params) -> dict:
    r = client.get(f"{BASE}.{path}", params={"output": "JSON", **params}, timeout=90)
    r.raise_for_status()
    return r.json().get("Results", {})


def _programs(f: dict) -> list[str]:
    """Which regulatory programs the facility is tracked under."""
    out = []
    if f.get("AIRFlag") == "Y" or f.get("CAAComplianceStatus"):
        out.append("air")
    if f.get("CWAComplianceStatus"):
        out.append("water")
    if f.get("RCRAComplianceStatus"):
        out.append("waste")
    if f.get("SDWAComplianceStatus"):
        out.append("drinking-water")
    if f.get("TRIFlag") == "Y":
        out.append("toxics-release")
    return out


def _status(f: dict) -> str:
    """Collapse ECHO's fields into one status bucket for filtering/coloring."""
    if f.get("FacSNCFlg") == "Y":
        return "significant_violation"
    cs = (f.get("FacComplianceStatus") or "").lower()
    if "significant" in cs:
        return "significant_violation"
    if "violation" in cs and "no violation" not in cs:
        return "violation"
    if "no violation" in cs:
        return "compliant"
    return "unknown"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(state: str) -> dict:
    with httpx.Client(follow_redirects=True) as client:
        meta = _get(client, "get_facilities", p_st=state, p_maj="Y")
        qid = meta["QueryID"]
        total = int(meta.get("QueryRows") or 0)
        print(f"{state} major facilities: {total} (SV={meta.get('SVRows')}, CV={meta.get('CVRows')})")

        # compliance records (paged; ECHO returns 250/page by default)
        by_id: dict[str, dict] = {}
        page = 1
        while True:
            res = _get(client, "get_qid", qid=qid, pageno=page)
            facs = res.get("Facilities", [])
            if not facs:
                break
            for f in facs:
                if f.get("RegistryID"):
                    by_id[f["RegistryID"]] = f
            print(f"  page {page}: +{len(facs)} ({len(by_id)} total)")
            if len(facs) < 250:
                break
            page += 1

        # longitudes come from the CSV download (same qid), joined on RegistryID
        csv_txt = client.get(f"{BASE}.get_download", params={"qid": qid}, timeout=120).text
        lon_by_id = {row["RegistryID"]: _num(row.get("FacLong"))
                     for row in csv.DictReader(io.StringIO(csv_txt)) if row.get("RegistryID")}

    facilities = []
    for rid, f in by_id.items():
        lat, lon = _num(f.get("FacLat")), lon_by_id.get(rid)
        if lat is None or lon is None:
            continue
        facilities.append({
            "registry_id": rid,
            "name": (f.get("FacName") or "").strip().title(),
            "city": (f.get("FacCity") or "").title(),
            "county": (f.get("FacCounty") or "").title(),
            "lat": lat,
            "lon": lon,
            "status": _status(f),
            "compliance_status": f.get("FacComplianceStatus"),
            "snc": f.get("FacSNCFlg") == "Y",
            "active": f.get("FacActiveFlag") == "Y",
            "quarters_nc": f.get("FacQtrsWithNC"),
            "programs": _programs(f),
            "last_inspection": f.get("FacDateLastInspection"),
            "last_penalty": f.get("FacDateLastPenalty"),
            "naics": f.get("FacNAICSCodes"),
            "echo_url": f"https://echo.epa.gov/detailed-facility-report?fid={rid}",
        })
    facilities.sort(key=lambda x: (x["status"] != "significant_violation",
                                   x["status"] != "violation", x["name"]))
    return {
        "source": "EPA ECHO (Enforcement & Compliance History Online)",
        "state": state,
        "scope": "major regulated facilities",
        "disclaimer": ("Compliance status is EPA's official record and can lag real events. "
                       "'Significant violation' / 'violation' reflect EPA's tracking, not a "
                       "legal determination. Always confirm on the linked ECHO report."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "facilities": facilities,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="WV")
    args = ap.parse_args()
    data = fetch(args.state)
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    n = len(data["facilities"])
    from collections import Counter
    tally = Counter(f["status"] for f in data["facilities"])
    print(f"wrote {n} facilities → {OUT}")
    print("  by status:", dict(tally))


if __name__ == "__main__":
    main()
