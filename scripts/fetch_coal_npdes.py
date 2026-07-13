#!/usr/bin/env python3
"""Pull WV coal-mine water discharge permits (NPDES) — where mining meets water.

This is the "one facility, two media" payoff: coal mines don't just disturb land,
they discharge treated (and sometimes not-well-treated) water into streams — acid
mine drainage, iron/manganese, selenium, high conductivity. WV DEP's Coal NPDES
layer tracks every permitted discharge outlet and the stream that receives it.

There are ~22k active outlets, so we aggregate to the permit level (one coal
operator's discharge system): a representative location, how many outlets it runs,
and the streams it discharges into — a browsable, mappable set (~1,400 permits) that
links each operator to the water it affects.

Source: WV DEP TAGIS ArcGIS, mining_reclamation / "Coal NPDES" layer — keyless.
Links to each permit's EPA ECHO effluent charts (actual discharge measurements).

    python scripts/fetch_coal_npdes.py

Writes src/airwv/data/coal_npdes.json, served by /api/coal-npdes and shown as a map
overlay on the Water page + a section on Sources.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

LAYER = ("https://tagis.dep.wv.gov/arcgis/rest/services/"
         "WVDEP_enterprise/mining_reclamation/MapServer/2/query")
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "coal_npdes.json"


def _clean_streams(vals) -> list[str]:
    """Distinct, human-ish receiving-stream names (drop nulls; keep order-stable)."""
    seen, out = set(), []
    for v in vals:
        for part in (v or "").split(","):
            s = part.strip().strip(".").strip()
            key = s.lower()
            if len(s) >= 3 and any(ch.isalpha() for ch in s) and key not in seen:
                seen.add(key)
                out.append(s)
    return out


def fetch() -> dict:
    params = {
        "where": "status_flag='O' AND inspectable_unit_type='OUTLT'",  # active discharge outlets
        "outFields": "permit,responsible_party,receiving_stream,status_code,latitude,longitude",
        "returnGeometry": "false", "f": "json", "orderByFields": "permit",
    }
    by_permit: dict[str, dict] = defaultdict(lambda: {"lats": [], "lons": [], "streams": [], "op": "", "status": ""})
    offset = 0
    with httpx.Client(timeout=90) as client:
        while True:
            r = client.get(LAYER, params={**params, "resultOffset": offset, "resultRecordCount": 5000})
            r.raise_for_status()
            feats = r.json().get("features", [])
            if not feats:
                break
            for ft in feats:
                a = ft["attributes"]
                lat, lon = a.get("latitude"), a.get("longitude")
                if lat is None or lon is None:
                    continue
                p = by_permit[a.get("permit")]
                p["lats"].append(float(lat))
                p["lons"].append(float(lon))
                p["streams"].append(a.get("receiving_stream"))
                p["op"] = p["op"] or (a.get("responsible_party") or "").strip().title()
                p["status"] = p["status"] or a.get("status_code")
            offset += len(feats)
            print(f"  +{len(feats)} ({offset} outlets)")
            if len(feats) < 5000:
                break

    permits = []
    for permit, p in by_permit.items():
        if not p["lats"]:
            continue
        streams = _clean_streams(p["streams"])
        permits.append({
            "permit": permit,
            "operator": p["op"] or "Operator unknown",
            "outlets": len(p["lats"]),
            "receiving_streams": streams[:8],
            "stream_count": len(streams),
            "lat": round(sum(p["lats"]) / len(p["lats"]), 5),
            "lon": round(sum(p["lons"]) / len(p["lons"]), 5),
            "effluent_url": f"https://echo.epa.gov/effluent-charts/{permit}",
        })
    permits.sort(key=lambda x: -x["outlets"])
    return {
        "source": "WV DEP — Coal NPDES (mining water-discharge permits)",
        "scope": "active coal-mine discharge permits, aggregated by permit",
        "partner_note": ("Mining's water impacts are a focus of WV Rivers Coalition and "
                         "the FracTracker Alliance."),
        "disclaimer": ("A permitted discharge is legal and treated; it is not proof of "
                       "pollution. Outlet counts and receiving streams are WV DEP's record. "
                       "See each permit's EPA ECHO effluent charts for measured discharge."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "permits": permits,
    }


def main() -> None:
    data = fetch()
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tot_out = sum(p["outlets"] for p in data["permits"])
    print(f"wrote {len(data['permits'])} permits ({tot_out} outlets) → {OUT}")
    print("  top by outlets:", [(p["operator"][:22], p["outlets"]) for p in data["permits"][:3]])


if __name__ == "__main__":
    main()
