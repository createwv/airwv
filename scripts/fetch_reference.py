"""Pull WV regulatory-monitor PM2.5 from EPA AirData for the reference-monitor markers.

Downloads EPA's pre-generated daily PM2.5 file (parameter 88101), filters to West
Virginia, and writes the regulatory monitor sites + their annual mean PM2.5. These
validated, reference-grade monitors plot next to our community sensors to sanity-check
them. (Live reference is the `airnow` ingest; this is a lightweight annual snapshot.)

Source: EPA AirData / AQS annual daily files (aqs.epa.gov/aqsweb/airdata, keyless, no quota).

    python scripts/fetch_reference.py [year]   # default 2024

Writes src/airwv/data/reference_monitors.json, served by /api/reference-monitors.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx

WV_STATE_CODE = "54"


def main(year: str = "2024"):
    url = f"https://aqs.epa.gov/aqsweb/airdata/daily_88101_{year}.zip"
    print(f"downloading {url} ...")
    data = httpx.get(url, timeout=120).content
    zf = zipfile.ZipFile(io.BytesIO(data))
    csv_name = zf.namelist()[0]

    sites: dict[str, dict] = {}
    means: dict[str, list[float]] = defaultdict(list)
    with zf.open(csv_name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8"))
        for row in reader:
            if row.get("State Code") != WV_STATE_CODE:
                continue
            sid = f"{row['State Code']}-{row['County Code']}-{row['Site Num']}"
            try:
                mean = float(row["Arithmetic Mean"])
                lat = float(row["Latitude"])
                lon = float(row["Longitude"])
            except (ValueError, KeyError):
                continue
            means[sid].append(mean)
            sites.setdefault(sid, {
                "id": sid,
                "name": (row.get("Local Site Name") or row.get("City Name") or "").strip() or sid,
                "county": (row.get("County Name") or "").strip(),
                "lat": round(lat, 4),
                "lon": round(lon, 4),
            })

    monitors = []
    for sid, s in sorted(sites.items()):
        vals = means[sid]
        s["mean_pm25"] = round(sum(vals) / len(vals), 1)
        s["days"] = len(vals)
        s["citation"] = f"EPA AirData / AQS (monitor {sid}, {year})"
        monitors.append(s)

    out = {
        "kind": "reference",
        "note": f"EPA regulatory PM2.5 monitors in WV ({year}) — validated reference-grade "
                "data, shown to sanity-check community sensors.",
        "monitors": monitors,
    }
    path = Path(__file__).parent.parent / "src/airwv/data/reference_monitors.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"WV reference monitors: {len(monitors)} -> {path.name}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2024")
