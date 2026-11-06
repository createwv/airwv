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


def test_views_cover_tables_and_layers(tmp_path, monkeypatch):
    _, store = _client(tmp_path, monkeypatch)
    rm = store.read_model
    with rm.connect() as conn:
        kinds = dict(conn.exec_driver_sql("SELECT kind, count(*) FROM site GROUP BY kind").all())
        # every JSON layer and both tables land in `site`
        assert {"well", "facility", "permit", "spill_report", "water_system", "reference_monitor",
                "sensor", "gauge", "sample_site"} <= set(kinds)
        assert kinds["sensor"] == 2 and kinds["gauge"] == 1 and kinds["sample_site"] == 2
        # site_id is the identity; it only repeats where a source file repeats a row verbatim
        # (abandoned_wells today; /api/near has always listed both), or for dep_permits,
        # where one well's API number carries several permit applications
        dup = conn.exec_driver_sql(
            "SELECT site_id, count(DISTINCT attrs) FROM site WHERE layer != 'dep_permits' "
            "GROUP BY site_id HAVING count(*) > 1").all()
        assert all(n == 1 for _, n in dup)
        # measurement is tall: one row per non-null air field + every water row
        m = dict(conn.exec_driver_sql(
            "SELECT parameter, count(*) FROM measurement GROUP BY parameter").all())
        assert m["pm2_5"] == 6 and m["voc"] == 3 and m["ozone"] == 1 and m["iron"] == 2
        assert conn.exec_driver_sql("SELECT count(*) FROM measurement").scalar() == 6 + 3 + 1 + 5
        # records carry public-record state, each tied to a site in the `site` view
        rk = dict(conn.exec_driver_sql("SELECT kind, count(*) FROM record GROUP BY kind").all())
        assert {"orphan", "violation", "compliance_status", "impaired_303d", "permit_stage",
                "spill", "health_violation"} <= set(rk)
        orphans = json.loads((webapp.Path(webapp.__file__).parent.parent / "data"
                              / "abandoned_wells.json").read_text())["orphans"]
        assert rk["orphan"] == orphans
        dangling = conn.exec_driver_sql(
            "SELECT count(*) FROM record r WHERE NOT EXISTS (SELECT 1 FROM site s WHERE s.site_id = r.site_id)").scalar()
        assert dangling == 0


def test_reading_sites_filled_at_ingest(tmp_path, monkeypatch):
    _, store = _client(tmp_path, monkeypatch)
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    store.save_readings([
        # the monitor moves: the site follows its latest located reading, not an older one
        Reading(source="airnow", sensor_id="540250003", ts=t0 + timedelta(hours=2), pm2_5=7.0,
                lat=38.36, lon=-81.64),
        Reading(source="airnow", sensor_id="540250003", ts=t0 - timedelta(hours=5), pm2_5=5.0,
                lat=38.0, lon=-81.0),
        # a located PurpleAir sensor the resolved listing doesn't carry, and an AirData
        # monitor reference_monitors.json already lists (left to that layer)
        Reading(source="purpleair", sensor_id="999001", ts=t0, pm2_5=4.0, lat=38.4, lon=-81.6),
        Reading(source="epa_airdata", sensor_id="54-003-0003", ts=t0, pm2_5=8.0, lat=39.4481, lon=-77.9638),
        # AirData's copy of the AirNow monitor above (left to AirNow), and one AirNow lacks
        Reading(source="epa_airdata", sensor_id="54-025-0003", ts=t0, pm2_5=7.5, lat=38.36, lon=-81.64),
        Reading(source="epa_airdata", sensor_id="54-099-0001", ts=t0, pm2_5=6.5, lat=38.2, lon=-82.5)])
    store.name_reading_sites("airnow", {"540250003": "Test monitor", "nope": "unknown monitor"})
    with store.read_model.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT site_id, kind, name, lat, lon, attrs FROM site WHERE layer = 'reading_sites' "
            "ORDER BY site_id").all()
        # Charleston is AirNow 540390020 in `readings` and 54-039-0020 in
        # reference_monitors.json: one site, not two
        charleston = conn.exec_driver_sql(
            "SELECT site_id FROM site WHERE site_id IN "
            "('reference_monitor:540390020', 'reference_monitor:54-039-0020')").scalars().all()
    assert charleston == ["reference_monitor:54-039-0020"]
    assert [r[0] for r in rows] == ["reference_monitor:54-099-0001", "reference_monitor:540250003",
                                    "sensor:999001"]
    site_id, kind, name, lat, lon, attrs = rows[1]
    assert (kind, name, lat, lon) == ("reference_monitor", "Test monitor", 38.36, -81.64)
    attrs = json.loads(attrs)
    assert attrs["source"] == "airnow" and attrs["first_ts"][:13] == "2026-06-30 19"
    assert attrs["last_ts"][:13] == "2026-07-01 02"
    # a database with readings from before the table existed is back-filled once
    with store._engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM reading_sites")
    store.create_schema()
    with store._engine.connect() as conn:
        got = dict(conn.exec_driver_sql("SELECT sensor_id, lat FROM reading_sites").all())
    assert got["540250003"] == 38.36 and got["197127"] is None and len(got) == 8


def test_layers_reload_when_a_file_changes(tmp_path, monkeypatch):
    from airwv.storage.readmodel import ReadModel
    _, store = _client(tmp_path, monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    f = data / "nrc_spills.json"
    f.write_text(json.dumps({"source": "NRC", "fetched_at": "2026-01-01", "spills": [
        {"report_id": 1, "lat": 38.3, "lon": -81.6, "type": "Sheen"}]}))
    rm = ReadModel(store._engine, data_dir=data)
    with rm.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM site WHERE kind = 'spill_report'").scalar() == 1
    f.write_text(json.dumps({"source": "NRC", "fetched_at": "2026-02-01", "spills": []}))
    os.utime(f, ns=(f.stat().st_mtime_ns + 10**9,) * 2)
    with rm.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM site WHERE kind = 'spill_report'").scalar() == 0
        assert rm.provenance(conn, ["nrc_spills"])["nrc_spills"]["fetched_at"] == "2026-02-01"


def test_api_query(tmp_path, monkeypatch):
    c, store = _client(tmp_path, monkeypatch)
    store.save_readings([Reading(source="airnow", sensor_id="540250003", pm2_5=5.5, lat=38.36,
                                 lon=-81.64, ts=datetime(2026, 7, 1, tzinfo=timezone.utc))])
    r = c.get("/api/query?lat=38.3727&lon=-81.6540&km=8&param=pm2_5").json()
    assert r["count"] == len(r["sites"]) > 0 and r["place"]["km"] == 8
    mis = [s["mi"] for s in r["sites"]]
    assert mis == sorted(mis) and max(mis) <= 8 * 0.621371
    by_id = {s["site_id"]: s for s in r["sites"]}
    assert by_id["sensor:197127"]["latest"]["value"] == 10.04          # latest of 8.04, 9.04, 10.04
    assert by_id["sensor:197127"]["name"] == "Kanawha City"
    # provenance travels with the answer
    assert all(layer in r["provenance"] for layer in {s["layer"] for s in r["sites"]}
               if layer not in ("water_readings", "reading_sites"))
    # an AirNow monitor in `readings` is a site too, with its latest value; one
    # reference_monitors.json carries (Charleston, 54-039-0020) isn't listed twice
    assert by_id["reference_monitor:540250003"]["layer"] == "reading_sites"
    assert by_id["reference_monitor:540250003"]["latest"]["value"] == 5.5
    assert "reference_monitor:54-039-0020" in by_id and "reference_monitor:540390020" not in by_id
    # filters
    water = c.get("/api/query?lat=38.3727&lon=-81.6540&km=8&medium=water").json()["sites"]
    assert water and all(s["medium"] == "water" for s in water)
    g = c.get("/api/query?lat=38.3727&lon=-81.6540&km=8&kind=gauge,sample_site&param=iron").json()["sites"]
    assert {s["site_id"] for s in g} == {"gauge:03198000", "sample_site:S1"}
    assert {s["site_id"]: s["latest"] and s["latest"]["value"] for s in g}["sample_site:S1"] == 1.9
    viol = c.get("/api/query?lat=38.3727&lon=-81.6540&km=8&kind=facility").json()["sites"]
    assert any(rec["kind"] == "violation" for s in viol for rec in s["records"])
    assert c.get("/api/query?lat=38.3&lon=-81.6&km=999&limit=9999").json()["filters"]["limit"] == 500


def test_api_query_param_list_and_since(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    base = "/api/query?lat=38.3727&lon=-81.6540&km=8"
    # a comma list of params: the first one each site has wins, named in `latest`
    g = {s["site_id"]: s["latest"] for s in c.get(
        f"{base}&kind=gauge,sample_site&param=selenium,iron,conductance").json()["sites"]}
    assert g["sample_site:S1"]["value"] == 1.9 and g["sample_site:S1"]["parameter"] == "iron"
    assert g["gauge:03198000"]["parameter"] == "conductance"
    # since: each measured site's peak of the first param, and records dated on/after it
    r = c.get(f"{base}&kind=sensor&param=pm2_5&since=2026-06-30").json()
    peaks = {s["site_id"]: s["peak"] and s["peak"]["value"] for s in r["sites"]}
    assert peaks["sensor:197127"] == 10.04 and peaks["sensor:200001"] == 12.35
    assert r["filters"]["since"] == "2026-06-30" and r["changes"] == []
    assert c.get(f"{base}&kind=sensor&param=pm2_5&since=2026-07-02").json()["sites"][0]["peak"] is None
    ch = c.get("/api/query?lat=38.3727&lon=-81.6540&km=40&kind=spill_report,permit&since=2026-01-01"
               "&limit=500").json()["changes"]
    assert ch and all(x["effective"][:10] >= "2026-01-01" for x in ch)
    assert [x["effective"] for x in ch] == sorted((x["effective"] for x in ch), reverse=True)
    assert {x["site_kind"] for x in ch} <= {"spill_report", "permit"}
    assert "changes" not in c.get(base).json()
    assert c.get(f"{base}&since=July").status_code == 400
    assert c.get("/api/query?lat=38.3&lon=-81.6&km=999").json()["place"]["km"] == 40.25   # 25 mi


def test_place_page(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.get("/place?place=rutledge&km=8")
    assert r.status_code == 200 and "place.js" in r.text and "reportmodal" in r.text


def test_api_query_csv(tmp_path, monkeypatch):
    import csv
    import io
    c, _ = _client(tmp_path, monkeypatch)
    base = "/api/query?lat=38.3727&lon=-81.6540&km=8"
    js = c.get(f"{base}&param=pm2_5").json()
    r = c.get(f"{base}&param=pm2_5&format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["site_id"] for x in rows] == [s["site_id"] for s in js["sites"]]   # same answer, same order
    by_id = {x["site_id"]: x for x in rows}
    assert by_id["sensor:197127"]["value"] == "10.04" and by_id["sensor:197127"]["parameter"] == "pm2_5"
    # show=records: one row per record; with since, only the dated "changes"
    recs = list(csv.DictReader(io.StringIO(c.get(f"{base}&kind=facility&format=csv&show=records").text)))
    js = c.get(f"{base}&kind=facility").json()
    assert len(recs) == sum(len(s["records"]) for s in js["sites"]) > 0
    assert {"site_id", "record_kind", "effective", "url"} <= set(recs[0])
    big = "/api/query?lat=38.3727&lon=-81.6540&km=40&kind=spill_report,permit&since=2026-01-01&limit=500"
    ch = list(csv.DictReader(io.StringIO(c.get(f"{big}&format=csv&show=records").text)))
    assert [x["effective"] for x in ch] == [x["effective"] for x in c.get(big).json()["changes"]]
    assert c.get(f"{base}&format=xml").status_code == 400
    assert c.get(f"{base}&show=wells").status_code == 400


def test_explore_page(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.get("/explore?place=rutledge&km=8&param=pm2_5")
    assert r.status_code == 200 and "explore.js" in r.text and 'id="ex-csv"' in r.text
    assert 'id="ex-api"' in r.text and 'name="kind"' in r.text
