#!/usr/bin/env python3
"""Pull WV public drinking-water systems + Safe Drinking Water Act violations.

This is the dataset behind the drinking-water crisis in WV's southern coalfields —
[Wyoming, Boone, Mercer] rank among the worst in the nation for drinking-water
violations. The ambient stream feeds (WQP/USGS) don't capture it; this does: every
public water system, whether it has a *health-based* violation, how many people it
serves, its water source, and lead/copper status.

Source: EPA ECHO Safe Drinking Water (SDWIS) REST service — keyless. Systems carry a
county, not coordinates, so they're placed at the county centroid for the map.

    python scripts/fetch_sdwa.py                 # WV active systems
    python scripts/fetch_sdwa.py --state OH

Writes src/airwv/data/sdwa_systems.json, served by /api/sdwa and shown on the Water
page (county map + system list).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from airwv.wvgeo import WV_COUNTY_FIPS, canon_county, county_centroid

BASE = "https://echodata.epa.gov/echo/sdw_rest_services"
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "sdwa_systems.json"


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _county(sy: dict) -> str | None:
    c = canon_county((sy.get("CountiesServed") or "").split(",")[0])
    if c:
        return c
    fips = (sy.get("FIPSCodes") or "").split(",")[0].strip()
    return WV_COUNTY_FIPS.get(fips[-3:]) if len(fips) >= 3 else None


def fetch(state: str) -> dict:
    with httpx.Client(timeout=90) as client:
        meta = client.get(f"{BASE}.get_systems", params={"output": "JSON", "p_st": state}).json()["Results"]
        qid = meta["QueryID"]
        print(f"{state} public water systems: {meta.get('QueryRows')}")
        systems, page = [], 1
        while True:
            res = client.get(f"{BASE}.get_qid", params={"output": "JSON", "qid": qid, "pageno": page}).json()["Results"]
            rows = res.get("WaterSystems", [])
            if not rows:
                break
            systems.extend(rows)
            print(f"  page {page}: +{len(rows)} ({len(systems)})")
            if len(rows) < 250:
                break
            page += 1

    out = []
    for sy in systems:
        if sy.get("PWSActivityCode") != "A":          # active systems only
            continue
        county = _county(sy)
        cc = county_centroid(county)
        health = sy.get("HealthFlag") == "Yes"
        lead_copper = bool(sy.get("LeadAndCopperViol") or sy.get("PbViol") or sy.get("CuViol"))
        out.append({
            "pws_id": sy.get("PWSId"),
            "name": (sy.get("PWSName") or "").strip().title(),
            "type": sy.get("PWSTypeDesc"),
            "community": sy.get("PWSTypeCode") == "CWS",     # serves residents' homes
            "owner": sy.get("OwnerDesc"),
            "county": county,
            "population": _int(sy.get("PopulationServedCount")),
            "source": sy.get("PrimarySourceDesc"),
            "health_violation": health,
            "serious_violator": sy.get("SeriousViolator") == "Yes",
            "current_violation": sy.get("VioFlag") == "1" or sy.get("CurrVioFlag") == "1",
            "lead_copper_violation": lead_copper,
            "qtrs_with_violation": _int(sy.get("QtrsWithVio")),
            "contaminants": sy.get("SDWAContaminantsInViol3yr"),
            "dfr_url": sy.get("DfrUrl"),
            "lat": cc[0] if cc else None,
            "lon": cc[1] if cc else None,
        })
    out.sort(key=lambda s: (not s["health_violation"], not s["serious_violator"], -s["population"]))
    return {
        "source": "EPA ECHO / SDWIS — Safe Drinking Water Act public water systems",
        "scope": f"{state} active public water systems + violation status",
        "disclaimer": ("A 'health-based violation' is an EPA SDWA record (maximum contaminant "
                       "level or treatment-technique) over the compliance period; it does not "
                       "mean water is unsafe right now. Systems are placed at their county "
                       "center (they serve an area, not a point). See each system's ECHO record."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "systems": out,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="WV")
    args = ap.parse_args()
    data = fetch(args.state)
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    sysz = data["systems"]
    comm = [s for s in sysz if s["community"]]
    health = [s for s in sysz if s["health_violation"]]
    from collections import Counter
    by_county = Counter(s["county"] for s in health if s["county"])
    print(f"wrote {len(sysz)} active systems ({len(comm)} community) → {OUT}")
    print(f"  with a health-based violation: {len(health)} "
          f"(serving {sum(s['population'] for s in health):,} people)")
    print("  worst counties (health violations):", dict(by_county.most_common(6)))


if __name__ == "__main__":
    main()
