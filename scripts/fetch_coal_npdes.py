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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

LAYER = ("https://tagis.dep.wv.gov/arcgis/rest/services/"
         "WVDEP_enterprise/mining_reclamation/MapServer/2/query")
IMPAIRED = ("https://tagis.dep.wv.gov/arcgis/rest/services/"
            "WVDEP_enterprise/watershed_assessment/MapServer/15/query")
OUT = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "coal_npdes.json"

# 303(d) impairment cause columns → friendly label. Marked ● are the classic
# coal / acid-mine-drainage signatures we highlight.
CAUSE_LABELS = {
    "iron": ("Iron", True), "aluminum": ("Aluminum", True), "manganese": ("Manganese", True),
    "selenium": ("Selenium", True), "ph": ("pH (acidity)", True), "sedimentat": ("Sedimentation", True),
    "bio": ("Biological", True), "do_": ("Dissolved oxygen", True),
    "al_trout": ("Aluminum (trout)", True), "iron_trout": ("Iron (trout)", True),
    "fecal_coli": ("Fecal coliform", False), "bacteria": ("Bacteria", False),
    "chloride": ("Chloride", False), "cna_algae": ("Algae", False), "dioxin": ("Dioxin", False),
    "methylmerc": ("Methylmercury", False), "pcbs": ("PCBs", False), "ammonia": ("Ammonia", False),
    "phosphorus": ("Phosphorus", False), "chlorophyl": ("Chlorophyll", False),
    "beryllium": ("Beryllium", False),
}


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


def _impairment_for(client: httpx.Client, bbox) -> tuple[list, list]:
    """Query the 2016 303(d) impaired-streams layer over a permit's outlet bbox;
    return (impairment causes as labels, impaired stream names)."""
    pad = 0.008  # ~800 m, so an outlet just off the stream still catches it
    xmin, ymin, xmax, ymax = bbox
    env = f"{xmin - pad},{ymin - pad},{xmax + pad},{ymax + pad}"
    params = {
        "geometry": env, "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "finalnam_1," + ",".join(CAUSE_LABELS),
        "returnGeometry": "false", "f": "json",
    }
    try:
        feats = client.get(IMPAIRED, params=params, timeout=60).json().get("features", [])
    except Exception:
        return [], []
    causes: dict[str, bool] = {}
    streams: set[str] = set()
    for ft in feats:
        a = ft["attributes"]
        hit = False
        for col, (label, mining) in CAUSE_LABELS.items():
            if (a.get(col) or "").strip():
                causes[label] = mining
                hit = True
        if hit and (a.get("finalnam_1") or "").strip():
            streams.add(a["finalnam_1"].strip())
    # mining-signature causes first
    ordered = sorted(causes, key=lambda c: (not causes[c], c))
    return ordered, sorted(streams)


def _join_impairment(permits: list) -> None:
    with httpx.Client() as client:
        def enrich(p):
            causes, streams = _impairment_for(client, p["_bbox"])
            p["impaired"] = bool(causes)
            p["impairment_causes"] = causes
            p["impaired_streams"] = streams[:6]
        with ThreadPoolExecutor(max_workers=8) as pool:
            for i, _ in enumerate(pool.map(enrich, permits), 1):
                if i % 200 == 0:
                    print(f"  joined {i}/{len(permits)}")


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
            # bbox over the permit's outlets, for the 303(d) spatial join
            "_bbox": (min(p["lons"]), min(p["lats"]), max(p["lons"]), max(p["lats"])),
        })

    print(f"joining {len(permits)} permits to 303(d) impaired streams…")
    _join_impairment(permits)
    for p in permits:
        p.pop("_bbox", None)
    permits.sort(key=lambda x: (not x["impaired"], -x["outlets"]))
    return {
        "source": "WV DEP — Coal NPDES + 2016 303(d) impaired streams",
        "scope": "active coal-mine discharge permits, aggregated by permit, joined to 303(d) impaired streams",
        "partner_note": ("Mining's water impacts are a focus of WV Rivers Coalition and "
                         "the FracTracker Alliance."),
        "disclaimer": ("A permitted discharge is legal and treated; it is not proof of pollution. "
                       "'On an impaired stream' means a discharge outlet falls within ~800 m of a "
                       "segment on WV's 2016 Clean Water Act 303(d) list — it flags where to look, "
                       "not that this permit caused the impairment. See each permit's EPA ECHO "
                       "effluent charts for measured discharge."),
        "fetched_at": datetime.now(tz=timezone.utc).date().isoformat(),
        "permits": permits,
    }


def main() -> None:
    data = fetch()
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    from collections import Counter
    permits = data["permits"]
    tot_out = sum(p["outlets"] for p in permits)
    impaired = [p for p in permits if p["impaired"]]
    causes = Counter(c for p in impaired for c in p["impairment_causes"])
    print(f"wrote {len(permits)} permits ({tot_out} outlets) → {OUT}")
    print(f"  on 303(d)-impaired streams: {len(impaired)} permits")
    print("  top impairment causes:", dict(causes.most_common(6)))


if __name__ == "__main__":
    main()
