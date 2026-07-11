"""Build prevailing wind roses for WV from keyless ASOS data (Iowa Environmental
Mesonet). For each airport station we pull a year of wind observations and compute
the frequency the wind blows FROM each of 8 compass directions.

The source-proximity panel uses these to weight sensors by how often they sit
*downwind* of a given source (Level 1 wind weighting — see docs/WIND-AND-DISPERSION.md).

    python scripts/fetch_wind.py            # writes src/airwv/data/wind_roses.json

Keyless, no quota — like our EPA sources. Data © Iowa Environmental Mesonet / NWS.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx

YEAR = 2025  # a full recent year = a stable prevailing-wind climatology
DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# WV ASOS stations (id -> name, lat, lon). Spread across the state's regions.
STATIONS = {
    "CRW": ("Charleston", 38.3792, -81.5903),
    "HTS": ("Huntington", 38.3667, -82.5580),
    "PKB": ("Parkersburg", 39.3451, -81.4392),
    "CKB": ("Clarksburg", 39.2966, -80.2281),
    "MGW": ("Morgantown", 39.6429, -79.9161),
    "EKN": ("Elkins", 38.8894, -79.8571),
    "BKW": ("Beckley", 37.7873, -81.1242),
    "BLF": ("Bluefield", 37.2957, -81.2043),
    "LWB": ("Lewisburg", 37.8583, -80.3994),
    "MRB": ("Martinsburg", 39.4018, -77.9847),
}


def dir8(deg: float) -> str:
    return DIRS[round(deg / 45) % 8]


def fetch_rose(station: str) -> dict | None:
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
           f"station={station}&data=drct&data=sknt&year1={YEAR}&month1=1&day1=1"
           f"&year2={YEAR + 1}&month2=1&day2=1&tz=UTC&format=onlycomma&missing=M")
    try:
        text = httpx.get(url, timeout=120).text
    except Exception as exc:
        print(f"  {station}: fetch failed ({exc})")
        return None
    counts = dict.fromkeys(DIRS, 0)
    total = 0
    for row in csv.DictReader(io.StringIO(text)):
        drct, sknt = row.get("drct", "M"), row.get("sknt", "M")
        if drct in ("M", "") or sknt in ("M", ""):
            continue
        try:
            d, s = float(drct), float(sknt)
        except ValueError:
            continue
        if s < 2:  # calm — no meaningful direction
            continue
        counts[dir8(d)] += 1
        total += 1
    if total == 0:
        return None
    return {"obs": total, "rose": {k: round(v / total, 4) for k, v in counts.items()}}


def main():
    out = []
    for sid, (name, lat, lon) in STATIONS.items():
        print(f"fetching {sid} ({name}) ...")
        rose = fetch_rose(sid)
        if not rose:
            continue
        top = max(rose["rose"], key=rose["rose"].get)
        print(f"  {rose['obs']:,} obs · prevailing from {top} ({rose['rose'][top]:.0%})")
        out.append({"station": sid, "name": name, "lat": lat, "lon": lon, **rose})
    doc = {
        "note": f"Prevailing wind roses (frequency wind blows FROM each direction), {YEAR}. "
                "Source: Iowa Environmental Mesonet / NWS ASOS. Used to weight sensors by "
                "downwind exposure to a source (Level 1 — see docs/WIND-AND-DISPERSION.md).",
        "year": YEAR,
        "stations": out,
    }
    path = Path(__file__).parent.parent / "src/airwv/data/wind_roses.json"
    path.write_text(json.dumps(doc, indent=2))
    print(f"\n{len(out)} stations -> {path.name}")


if __name__ == "__main__":
    main()
