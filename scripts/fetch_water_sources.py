"""Add WV water dischargers (NPDES) to the Sources dataset from EPA ECHO (keyless).

Makes the Sources page multi-medium: alongside air/TRI emitters, it now shows facilities
permitted to discharge to water under the Clean Water Act's NPDES program — each linked to
its full EPA ECHO compliance record (which covers air + water + waste). Idempotent: replaces
any prior ``category == "water_discharge"`` entries.

    python scripts/fetch_water_sources.py
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

SOURCES = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "sources.json"
ECHO = "https://echodata.epa.gov/echo/cwa_rest_services"
_KEEP = {"WWTP", "STP", "WTP", "POTW", "LLC", "INC", "LP", "LLP", "USA", "US", "WV",
         "AC&S", "APG", "ABL", "MRO", "AEP"}


def nicecase(s: str) -> str:
    out = []
    for w in s.split():
        u = w.strip(",.").upper()
        if u in _KEEP or (w.isupper() and len(w) <= 4 and not any(c in "AEIOU" for c in w)):
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out).replace(" Inc.", " Inc.")


def fetch() -> list[dict]:
    qid = httpx.get(f"{ECHO}.get_facilities",
                    params={"output": "JSON", "p_st": "wv", "p_maj": "Y"}, timeout=120).json()["Results"]["QueryID"]
    gj = httpx.get(f"{ECHO}.get_geojson", params={"output": "GEOJSON", "qid": qid}, timeout=120).json()
    out = []
    for f in gj.get("features", []):
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        p = f["properties"]
        permit = (p.get("SourceID") or "").strip()
        if not coords or not permit:
            continue
        lon, lat = coords[0], coords[1]
        name = nicecase((p.get("CWPName") or permit).strip())
        flow = p.get("CWPActualAverageFlowNmbr")
        flowtxt = f" (avg flow {flow} MGD)" if flow else ""
        county = (p.get("CWPCounty") or "").title()
        out.append({
            "name": name,
            "type": f"NPDES-permitted water discharge{flowtxt}",
            "operator": name,
            "state": "WV",
            "lat": round(float(lat), 5), "lon": round(float(lon), 5),
            "citation": f"EPA ECHO / NPDES permit {permit}"
                        + (f" · {county} County" if county else ""),
            "category": "water_discharge",
            "permit": permit,
            "echo": f"https://echo.epa.gov/detailed-facility-report?fid={permit}",
        })
    return out


def main() -> None:
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    kept = [s for s in data["sources"] if s.get("category") != "water_discharge"]
    water = fetch()
    data["sources"] = kept + water
    SOURCES.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"sources.json: {len(kept)} non-water kept + {len(water)} NPDES dischargers = {len(data['sources'])}")


if __name__ == "__main__":
    main()
