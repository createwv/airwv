#!/usr/bin/env python3
"""Pull WV's abandoned & orphaned oil/gas wells — the Rutledge story, statewide.

Abandoned wells dot West Virginia and leak — natural gas, and sometimes hydrogen
sulfide (H2S) — into yards and homes (the Rutledge neighborhood of Charleston is the
signature case, and a founding motivation for Create WV). "Orphan" wells have no
known operator, so the state is responsible for plugging them — and can only afford
~1–2 per year against a backlog of thousands.

Source: WV DEP TAGIS ArcGIS, oil_gas / "All DEP Wells", wellstatus='Abandoned Well'
(keyless). ~15,400 wells; ~4,700 are orphans. Compact records — the DEP/WVGS record
URL is reconstructed client-side from the permit id.

    python scripts/fetch_abandoned_wells.py

Writes src/airwv/data/abandoned_wells.json, served by /api/abandoned-wells and shown
as a (lazy) map layer on the Air dashboard.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from airwv.wvgeo import WV_COUNTY_FIPS

LAYER = ("https://tagis.dep.wv.gov/arcgis/rest/services/"
         "WVDEP_enterprise/oil_gas/MapServer/7/query")
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "abandoned_wells.json"

_ORPHAN_MARKERS = ("", "UNKNOWN", "ORPHAN", "OPERATOR UNKNOWN")


def _is_orphan(respparty: str | None) -> bool:
    r = (respparty or "").strip().upper()
    return r in _ORPHAN_MARKERS or "UNKNOWN" in r or "ORPHAN" in r


def fetch() -> dict:
    params = {
        "where": "wellstatus='Abandoned Well'",
        "outFields": "permitid,respparty,county", "returnGeometry": "true",
        "outSR": "4326", "f": "json", "orderByFields": "permitid",
    }
    page_size = 3000                               # server maxRecordCount for this layer
    wells, offset = [], 0
    with httpx.Client(timeout=120) as client:
        while True:
            r = client.get(LAYER, params={**params, "resultOffset": offset, "resultRecordCount": page_size})
            r.raise_for_status()
            feats = r.json().get("features", [])
            if not feats:
                break
            for ft in feats:
                a, g = ft["attributes"], ft.get("geometry") or {}
                if g.get("x") is None or g.get("y") is None:
                    continue
                orphan = _is_orphan(a.get("respparty"))
                wells.append({
                    "id": a.get("permitid"),
                    "lat": round(g["y"], 5), "lon": round(g["x"], 5),
                    "orphan": orphan,
                    "operator": None if orphan else (a.get("respparty") or "").strip().title(),
                    "county": WV_COUNTY_FIPS.get((a.get("county") or "").zfill(3), a.get("county")),
                })
            offset += len(feats)
            print(f"  +{len(feats)} ({offset})")
            if len(feats) < page_size:
                break

    orphans = sum(w["orphan"] for w in wells)
    return {
        "source": "WV DEP — Office of Oil & Gas (abandoned wells)",
        "scope": "wells with wellstatus 'Abandoned Well'; orphan = no known operator (state's to plug)",
        "disclaimer": ("Not every abandoned well leaks, and locations are as WV DEP records them. "
                       "'Orphan' means no responsible operator — the state is responsible for "
                       "plugging it. H2S/gas risk is highest for wells near homes."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "count": len(wells), "orphans": orphans,
        "wells": wells,
    }


def main() -> None:
    data = fetch()
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    from collections import Counter
    by_county = Counter(w["county"] for w in data["wells"] if w["orphan"])
    kb = len(OUT.read_bytes()) // 1024
    print(f"wrote {data['count']} abandoned wells ({data['orphans']} orphans) → {OUT} ({kb} KB)")
    print("  most orphan wells:", dict(by_county.most_common(6)))


if __name__ == "__main__":
    main()
