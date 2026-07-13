#!/usr/bin/env python3
"""Pull WV DEP oil & gas permit *pipeline* — the forward-looking lifecycle EPA
ECHO can't show: wells being requested, approved, or under construction.

EPA ECHO tells you a facility's compliance *today*. WV DEP's oil & gas permit
database tells you what's *coming*: new permit applications (requested), permits
issued but not yet drilled (approved), and wells under construction. For a
frac-heavy state that's the story communities most want ahead of time.

Source: WV DEP TAGIS ArcGIS (tagis.dep.wv.gov), oil_gas / "All DEP Wells" layer —
public, keyless. We keep the pre-production lifecycle statuses (plus recently
issued permits) and drop the 150k historical active/plugged wells.

    python scripts/fetch_dep_permits.py                 # pending + issued since 2022
    python scripts/fetch_dep_permits.py --since-year 2020

Writes src/airwv/data/dep_permits.json, served by /api/dep-permits and shown as a
map layer on the Air dashboard + a section on Sources.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import httpx

LAYER = ("https://tagis.dep.wv.gov/arcgis/rest/services/"
         "WVDEP_enterprise/oil_gas/MapServer/7/query")
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "dep_permits.json"

# WV FIPS county code (3-digit, as DEP stores it) → name.
WV_COUNTY = {
    "001": "Barbour", "003": "Berkeley", "005": "Boone", "007": "Braxton", "009": "Brooke",
    "011": "Cabell", "013": "Calhoun", "015": "Clay", "017": "Doddridge", "019": "Fayette",
    "021": "Gilmer", "023": "Grant", "025": "Greenbrier", "027": "Hampshire", "029": "Hancock",
    "031": "Hardy", "033": "Harrison", "035": "Jackson", "037": "Jefferson", "039": "Kanawha",
    "041": "Lewis", "043": "Lincoln", "045": "Logan", "047": "McDowell", "049": "Marion",
    "051": "Marshall", "053": "Mason", "055": "Mercer", "057": "Mineral", "059": "Mingo",
    "061": "Monongalia", "063": "Monroe", "065": "Morgan", "067": "Nicholas", "069": "Ohio",
    "071": "Pendleton", "073": "Pleasants", "075": "Pocahontas", "077": "Preston", "079": "Putnam",
    "081": "Raleigh", "083": "Randolph", "085": "Ritchie", "087": "Roane", "089": "Summers",
    "091": "Taylor", "093": "Tucker", "095": "Tyler", "097": "Upshur", "099": "Wayne",
    "101": "Webster", "103": "Wetzel", "105": "Wirt", "107": "Wood", "109": "Wyoming",
}

# wellstatus → lifecycle stage we present.
STAGE = {
    "Permit Application": "requested",
    "Under Construction": "construction",
    "Permit Issued": "approved",
    "Future Use": "approved",
}


def _webmerc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = x / 20037508.34 * 180
    lat = math.degrees(2 * math.atan(math.exp((y / 20037508.34 * 180) * math.pi / 180)) - math.pi / 2)
    return round(lon, 5), round(lat, 5)


def _date(s: str | None) -> str | None:
    return s.replace("/", "-") if s else None   # 2024/03/12 → 2024-03-12


def fetch(since_year: int) -> dict:
    # pending statuses regardless of date; issued/future-use only if recent.
    where = ("wellstatus IN ('Permit Application','Under Construction') "
             f"OR (wellstatus IN ('Permit Issued','Future Use') AND issuedate >= '{since_year}/01/01')")
    params = {
        "where": where,
        "outFields": "permitid,county,respparty,welltype,permittype,wellstatus,"
                     "issuedate,recdate,formation,marcellus,link",
        "returnGeometry": "true", "outSR": "3857", "f": "json",
        "orderByFields": "issuedate DESC",
    }
    permits, offset = [], 0
    with httpx.Client(timeout=90) as client:
        while True:
            r = client.get(LAYER, params={**params, "resultOffset": offset, "resultRecordCount": 2000})
            r.raise_for_status()
            feats = r.json().get("features", [])
            if not feats:
                break
            for ft in feats:
                a, g = ft["attributes"], ft.get("geometry") or {}
                if g.get("x") is None:
                    continue
                lon, lat = _webmerc_to_lonlat(g["x"], g["y"])
                st = a.get("wellstatus")
                operator = (a.get("respparty") or "").strip().title()
                permits.append({
                    "permit_id": a.get("permitid"),
                    "stage": STAGE.get(st, "other"),
                    "well_status": st,
                    "operator": operator if operator and operator.upper() != "OPERATOR UNKNOWN" else "Operator unknown",
                    "county": WV_COUNTY.get((a.get("county") or "").zfill(3), a.get("county")),
                    "well_type": a.get("welltype") or a.get("permittype"),
                    "formation": None if (a.get("formation") in (None, "NA")) else a.get("formation"),
                    "marcellus": a.get("marcellus") == "Y",
                    "issue_date": _date(a.get("issuedate")),
                    "received_date": _date(a.get("recdate")),
                    "lat": lat, "lon": lon,
                    "link": a.get("link"),
                })
            offset += len(feats)
            print(f"  +{len(feats)} ({offset} total)")
            if len(feats) < 2000:
                break

    permits.sort(key=lambda p: (p["stage"] != "requested", p["stage"] != "construction",
                                p["issue_date"] or "", ), reverse=False)
    return {
        "source": "WV DEP (Department of Environmental Protection) — Office of Oil & Gas",
        "partner_note": ("Oil & gas is also mapped with strong storytelling by the "
                         "FracTracker Alliance, a WV partner."),
        "scope": f"oil & gas permit pipeline — pending, plus permits issued since {since_year}",
        "disclaimer": ("Permit stage is WV DEP's record and can lag. A 'requested' or "
                       "'approved' permit is not a guarantee a well is drilled. Always "
                       "confirm on the linked DEP/WVGES record."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "permits": permits,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-year", type=int, default=2022)
    args = ap.parse_args()
    data = fetch(args.since_year)
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    from collections import Counter
    tally = Counter(p["stage"] for p in data["permits"])
    print(f"wrote {len(data['permits'])} permits → {OUT}")
    print("  by stage:", dict(tally))


if __name__ == "__main__":
    main()
