"""The unified read model (SQL views ``site`` / ``measurement`` / ``record``) and the
endpoints built on it. The /api/near snapshot was recorded from the pre-views
implementation; the views-based one must reproduce it byte for byte.

Regenerate (only when the committed data files change on purpose; a missing file is
re-recorded too):
    AIRWV_UPDATE_SNAPSHOTS=1 .venv/bin/python -m pytest tests/test_readmodel.py
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from airwv.sources.base import Reading
from airwv.storage import Store
from airwv.web import app as webapp

SNAPSHOT = Path(__file__).parent / "snapshots" / "api_near.json"

# (lat, lon, km): Charleston, Rutledge (demo place, approximate), the coalfields,
# the northern gas field, a big radius, a clamped radius, and a point outside WV.
NEAR_POINTS = [
    (38.3727, -81.6540, 8), (38.383, -81.585, 8), (37.805, -81.905, 8), (37.85, -81.99, 25),
    (39.28, -80.35, 8), (39.65, -80.02, 15), (38.3, -81.6, 999), (38.95, -80.6, 0.2),
    (40.7, -74.0, 8),
]


def _client(tmp_path, monkeypatch):
    # two community sensors (coords/names/regions normally come from the resolved listing)
    monkeypatch.setattr(webapp, "_index_to_coords", lambda: {
        "197127": (38.3650, -81.6400), "200001": (38.3800, -81.5900), "300003": (39.0, -80.0)})
    monkeypatch.setattr(webapp, "_index_to_name", lambda: {"197127": "Kanawha City", "200001": "Rutledge"})
    monkeypatch.setattr(webapp, "_index_to_region", lambda: {
        "197127": "Kanawha Valley", "200001": "Kanawha Valley"})
    store = Store(f"sqlite:///{tmp_path / 'rm.sqlite'}")
    store.create_schema()
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    store.save_readings(
        [Reading(source="purpleair", sensor_id="197127", ts=t0 + timedelta(hours=i),
                 pm2_5=8.04 + i, voc=97.6 + i) for i in range(3)]
        + [Reading(source="purpleair", sensor_id="200001", ts=t0 + timedelta(hours=i),
                   pm2_5=12.35 if i < 2 else None) for i in range(3)]
        + [Reading(source="airnow", sensor_id="540390020", ts=t0, pm2_5=6.0, ozone=31.0,
                   lat=38.35, lon=-81.63)])
    w = dict(ingested_at=t0)
    store.add_water_readings([
        {"source": "wqp", "site_id": "S1", "site_name": "Elk River at Clendenin", "lat": 38.37,
         "lon": -81.62, "ts": t0, "parameter": "iron", "value": 2.4, "unit": "mg/L", **w},
        {"source": "wqp", "site_id": "S1", "site_name": "Elk River at Clendenin", "lat": 38.37,
         "lon": -81.62, "ts": t0 + timedelta(days=1), "parameter": "iron", "value": 1.9, "unit": "mg/L", **w},
        {"source": "wqp", "site_id": "S1", "site_name": "Elk River at Clendenin", "lat": 38.37,
         "lon": -81.62, "ts": t0, "parameter": "ph", "value": 7.1, "unit": "pH", **w},
        {"source": "usgs", "site_id": "03198000", "site_name": "Kanawha River at Charleston",
         "lat": 38.371, "lon": -81.702, "ts": t0, "parameter": "conductance", "value": 310.0,
         "unit": "uS/cm", **w},
        {"source": "wqp", "site_id": "S2", "site_name": "Spruce Fork", "lat": 37.81, "lon": -81.90,
         "ts": t0, "parameter": "selenium", "value": 6.5, "unit": "ug/L", **w},
    ])
    return TestClient(webapp.create_app(store)), store


def _near_all(c):
    return {f"{la},{lo},{km}": c.get(f"/api/near?lat={la}&lon={lo}&km={km}").json()
            for la, lo, km in NEAR_POINTS}


def test_api_near_matches_snapshot(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    got = _near_all(c)
    if os.environ.get("AIRWV_UPDATE_SNAPSHOTS") or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                            encoding="utf-8")
    want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert set(got) == set(want)
    for key in want:
        assert got[key] == want[key], key
    # the snapshot actually exercises every hazard category
    cats = {h["category"] for r in want.values() for h in r["hazards"]}
    assert cats == {"gas", "air", "water", "chemical", "sensor"}
