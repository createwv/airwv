#!/usr/bin/env python3
"""Pull WV DEP coal/mineral mining permits — the active + upcoming operations.

Coal is West Virginia's other big environmental story: surface mining, acid mine
drainage, blasting near homes, and the long arc of disturbance → reclamation. WV
DEP's Division of Mining & Reclamation (DMR) tracks every permit and its lifecycle.

We keep the *live* permits — newly issued / not-yet-started (upcoming), active /
renewed (operating), and inactive (idle) — and drop the ~8,200 "completely
released" / revoked / terminated historical permits, so the layer shows what's
happening now, not decades of closed sites.

Source: WV DEP TAGIS ArcGIS, mining_reclamation / "DMR permits" layer — keyless.
Includes disturbed vs. reclaimed acreage where DEP reports it.

    python scripts/fetch_dep_mining.py

Writes src/airwv/data/dep_mining.json, served by /api/dep-mining and shown as a map
layer on the Air dashboard + a section on Sources.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

LAYER = ("https://tagis.dep.wv.gov/arcgis/rest/services/"
         "WVDEP_enterprise/mining_reclamation/MapServer/0/query")
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "dep_mining.json"

# per_status → lifecycle stage we present. Anything not listed (Completely/Phase
# Released, Revoked, Terminated, Closed, Rescinded, NA) is historical → dropped.
STAGE = {
    "New": "new", "Not Started": "new", "Not Started Extended": "new",
    "Not Started Ext Requested": "new",
    "Active": "active", "Renewed": "active", "Reinstated": "active",
    "Re-Activated after Release": "active",
    "Inactive": "inactive",
}


def _date(s: str | None) -> str | None:
    return s.replace("/", "-") if s else None


def _acres(v) -> float | None:
    try:
        f = float(v)
        return round(f, 1) if f > 0 else None   # -1 / 0 = not reported
    except (TypeError, ValueError):
        return None


def fetch() -> dict:
    keep = ",".join(f"'{s}'" for s in STAGE)
    params = {
        "where": f"per_status IN ({keep})",
        "outFields": "permit_id,permittee,facility,type,per_status,inspstatus,"
                     "issuedate,expiredate,acres_dist,acres_recl,latitude,longitude",
        "returnGeometry": "false", "f": "json", "orderByFields": "issuedate DESC",
    }
    mines, offset = [], 0
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
                inv = (a.get("inspstatus") or "").strip()
                mines.append({
                    "permit_id": a.get("permit_id"),
                    "stage": STAGE.get(a.get("per_status"), "other"),
                    "permit_status": a.get("per_status"),
                    "operator": (a.get("permittee") or "").strip().title(),
                    "facility": (a.get("facility") or "").strip().title(),
                    "type": a.get("type"),
                    "inspection_status": None if inv in ("", "NA", "UK-Unknown") else inv,
                    "acres_disturbed": _acres(a.get("acres_dist")),
                    "acres_reclaimed": _acres(a.get("acres_recl")),
                    "issue_date": _date(a.get("issuedate")),
                    "expire_date": _date(a.get("expiredate")),
                    "lat": round(float(lat), 5), "lon": round(float(lon), 5),
                })
            offset += len(feats)
            print(f"  +{len(feats)} ({offset} total)")
            if len(feats) < 5000:
                break

    mines.sort(key=lambda m: (m["stage"] != "new", m["stage"] != "active",
                              -(m["acres_disturbed"] or 0)))
    return {
        "source": "WV DEP (Department of Environmental Protection) — Division of Mining & Reclamation",
        "partner_note": ("Coal & mining impacts are also mapped by partners like the "
                         "FracTracker Alliance and WV Rivers Coalition."),
        "scope": "active + upcoming mining permits (released/revoked historical permits excluded)",
        "disclaimer": ("Permit stage is WV DEP's record and can lag. Acreage is disturbed vs. "
                       "reclaimed as DEP reports it (blank where not reported). Confirm details "
                       "on WV DEP's permit search."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "mines": mines,
    }


def main() -> None:
    data = fetch()
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    from collections import Counter
    stages = Counter(m["stage"] for m in data["mines"])
    types = Counter(m["type"] for m in data["mines"])
    print(f"wrote {len(data['mines'])} mines → {OUT}")
    print("  by stage:", dict(stages))
    print("  top types:", dict(types.most_common(5)))


if __name__ == "__main__":
    main()
