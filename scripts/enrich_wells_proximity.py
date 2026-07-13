#!/usr/bin/env python3
"""Flag abandoned wells by how close they sit to buildings — the near-homes risk.

A leaking well in a remote hollow is a different problem from one in someone's yard.
This enriches abandoned_wells.json with each well's distance to the nearest building,
using Microsoft's open US Building Footprints (ML-derived from imagery, ~1.05M
buildings in WV — far more complete in rural areas than OSM). "Near homes" = within
200 m of a building.

Runs locally (the 273 MB footprints file is streamed, never committed); only the
small enriched JSON is committed and served.

    python scripts/enrich_wells_proximity.py

Caveat: "building" includes barns/sheds, not just homes — it's a structures-nearby
proxy, stated as such in the UI.
"""
from __future__ import annotations

import io
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx

BUILDINGS_URL = ("https://minedbuildings.z5.web.core.windows.net/legacy/"
                 "usbuildings-v2/WestVirginia.geojson.zip")
CACHE = Path("/tmp/wvbuild/WestVirginia.geojson")
WELLS = Path(__file__).resolve().parent.parent / "src" / "airwv" / "data" / "abandoned_wells.json"

CELL = 0.01           # ~1.1 km grid cell
NEAR_M = 200          # within this many metres of a building → "near homes"


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dl / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _ensure_buildings() -> Path:
    if CACHE.exists():
        return CACHE
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    print("downloading WV building footprints (~37 MB)…")
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        data = c.get(BUILDINGS_URL, headers={"User-Agent": "Mozilla/5.0 (AirWV)"}).content
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.endswith(".geojson"))
        CACHE.write_bytes(z.read(name))
    return CACHE


def _load_grid(path: Path) -> dict:
    """Stream the line-delimited GeoJSON; index each building's first vertex."""
    grid: dict = defaultdict(list)
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith('{"type":"Feature"'):
                continue
            try:
                lon, lat = json.loads(line)["geometry"]["coordinates"][0][0]
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            grid[(int(lat / CELL), int(lon / CELL))].append((lat, lon))
            n += 1
            if n % 200000 == 0:
                print(f"  indexed {n:,} buildings…")
    print(f"  indexed {n:,} buildings")
    return grid


def _nearest_m(grid: dict, lat: float, lon: float) -> float | None:
    ci, cj = int(lat / CELL), int(lon / CELL)
    best = None
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for blat, blon in grid.get((ci + di, cj + dj), ()):
                d = _haversine_m(lat, lon, blat, blon)
                if best is None or d < best:
                    best = d
    return best if best is not None else None   # None = no building within ~1 km


def main() -> None:
    grid = _load_grid(_ensure_buildings())
    data = json.loads(WELLS.read_text(encoding="utf-8"))
    wells = data["wells"]
    near = 0
    for w in wells:
        d = _nearest_m(grid, w["lat"], w["lon"])
        w["nearest_building_m"] = round(d) if d is not None else None
        w["near_homes"] = d is not None and d <= NEAR_M
        near += w["near_homes"]
    orphans_near = sum(1 for w in wells if w["orphan"] and w["near_homes"])
    data["near_homes"] = near
    data["orphans_near_homes"] = orphans_near
    data["near_homes_m"] = NEAR_M
    WELLS.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(f"{near:,} of {len(wells):,} abandoned wells within {NEAR_M} m of a building "
          f"({orphans_near:,} of them orphans)")


if __name__ == "__main__":
    main()
