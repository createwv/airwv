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
from airwv.wvgeo import county_names

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
    # `page` (each hazard's /site/<kind>/<id>) came after the snapshot; everything else is pinned
    for r in got.values():
        for h in r["hazards"]:
            h.pop("page")
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


def test_api_near_hazards_have_site_pages(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    pages = {h["page"] for r in _near_all(c).values() for h in r["hazards"]}
    kinds = {p.split("/")[2] for p in pages}
    assert {"well", "facility", "permit", "spill_report", "water_system", "sensor",
            "gauge", "sample_site"} <= kinds
    for p in sorted(pages):
        r = c.get(p)
        assert r.status_code == 200, p
        if p.split("/")[2] not in webapp.SERIES_KINDS:   # those have the chart page
            assert "Plain summary" in r.text and "Full record" in r.text, p
        assert c.get(p + "?view=adv").status_code == 200, p


def test_site_pages_for_every_kind(tmp_path, monkeypatch):
    c, store = _client(tmp_path, monkeypatch)
    with store.read_model.connect() as conn:
        firsts = conn.exec_driver_sql(
            "SELECT kind, min(ref) FROM site WHERE ref IS NOT NULL GROUP BY kind").all()
    from airwv.web.app import SITE_KINDS, site_page_url
    assert {k for k, _ in firsts} <= set(SITE_KINDS)
    for kind, ref in firsts:
        assert c.get(site_page_url(kind, ref)).status_code == 200, (kind, ref)
    # a water system's health-based violation is listed in plain words
    sy = next(s for s in json.loads((webapp.Path(webapp.__file__).parent.parent / "data" / "sdwa_systems.json")
                                    .read_text(encoding="utf-8"))["systems"] if s.get("health_violation"))
    page = c.get(site_page_url("water_system", sy["pws_id"])).text
    assert "Health-based drinking-water violation" in page
    assert c.get("/site/well/no-such-well").status_code == 404
    assert c.get("/site/nonsense/1").status_code == 404


def test_views_cover_tables_and_layers(tmp_path, monkeypatch):
    _, store = _client(tmp_path, monkeypatch)
    rm = store.read_model
    with rm.connect() as conn:
        kinds = dict(conn.exec_driver_sql("SELECT kind, count(*) FROM site GROUP BY kind").all())
        # every JSON layer and both tables land in `site`
        assert {"well", "facility", "permit", "spill_report", "water_system", "reference_monitor",
                "sensor", "gauge", "sample_site"} <= set(kinds)
        assert kinds["sensor"] == 2 and kinds["gauge"] == 1 and kinds["sample_site"] == 2
        # site_id is the identity; it only repeats where a source file repeats a row verbatim,
        # or for dep_permits, where one well's API number carries several permit applications
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
    # the measurements option (series mode): its site and window fields
    assert 'value="measurements"' in r.text and 'name="site_id"' in r.text
    assert 'name="from"' in r.text and 'name="to"' in r.text
    # the county and value filters; McDowell keeps its capital D
    assert 'name="county"' in r.text and '<option value="McDowell">' in r.text and 'name="over"' in r.text


# /api/water/near and /api/areas moved onto the read model too; these snapshots were
# recorded from their pre-views implementations (store.water_near, store.daily_avg).
WATER_SNAPSHOT = Path(__file__).parent / "snapshots" / "api_water_near.json"
AREAS_SNAPSHOT = Path(__file__).parent / "snapshots" / "api_areas.json"

# (lat, lon, km, limit): Charleston, the coalfields, a tight radius, a clamped radius
# and limit, a limit of one, and a point outside WV.
WATER_POINTS = [
    (38.3727, -81.6540, 8, 6), (37.805, -81.905, 8, 6), (38.37, -81.62, 1, 6),
    (38.3, -81.6, 999, 99), (38.3727, -81.6540, 8, 1), (40.7, -74.0, 8, 6),
]
NOW = datetime(2026, 7, 31, 12, 0)   # /api/areas windows from utcnow(); frozen for the snapshot


def _check_snapshot(path, got):
    if os.environ.get("AIRWV_UPDATE_SNAPSHOTS") or not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(got, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
    want = json.loads(path.read_text(encoding="utf-8"))
    assert set(got) == set(want)
    for key in want:
        assert got[key] == want[key], key
    return want


def test_api_water_near_matches_snapshot(tmp_path, monkeypatch):
    c, store = _client(tmp_path, monkeypatch)
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    store.add_water_readings([   # a second sample site near Charleston, two visits
        {"source": "wqp", "site_id": "S3", "site_name": "Davis Creek", "lat": 38.33, "lon": -81.70,
         "ts": t0 - timedelta(days=40), "parameter": "ecoli", "value": 410.0, "unit": "cfu/100mL",
         "ingested_at": t0},
        {"source": "wqp", "site_id": "S3", "site_name": "Davis Creek", "lat": 38.33, "lon": -81.70,
         "ts": t0 - timedelta(days=3), "parameter": "ecoli", "value": 88.0, "unit": "cfu/100mL",
         "ingested_at": t0},
    ])
    got = {f"{la},{lo},{km},{n}": c.get(f"/api/water/near?lat={la}&lon={lo}&km={km}&limit={n}").json()
           for la, lo, km, n in WATER_POINTS}
    want = _check_snapshot(WATER_SNAPSHOT, got)
    assert any(len(r["sites"]) > 1 for r in want.values())


def _areas_client(tmp_path, monkeypatch):
    _, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(webapp, "_index_to_region", lambda: {
        "197127": "Kanawha Valley", "200001": "Kanawha Valley", "300003": "Mid-Ohio Valley",
        "400004": "Other"})
    # a month of daily readings: Kanawha City climbs (40-70 days ago), the Mid-Ohio sensor
    # eases off; its latest reading and one more the day before are PM spikes over
    # 1000 µg/m³, which /api/areas drops from the current value and the daily trend
    days = [NOW - timedelta(days=d) for d in range(30, 0, -1)]
    utc = timezone.utc
    store.save_readings(
        [Reading(source="purpleair", sensor_id="197127", ts=(t - timedelta(days=40)).replace(tzinfo=utc),
                 pm2_5=5.0 + i * 0.5, voc=80.0 + i, humidity=60.0) for i, t in enumerate(days)]
        + [Reading(source="purpleair", sensor_id="300003", ts=t.replace(tzinfo=utc),
                   pm2_5=20.0 - i * 0.3, voc=150.0 - i, humidity=55.0 + i % 3) for i, t in enumerate(days)]
        + [Reading(source="purpleair", sensor_id="300003", ts=NOW.replace(tzinfo=utc),
                   pm2_5=4000.0, voc=120.0),
           Reading(source="purpleair", sensor_id="300003", ts=(days[-1] + timedelta(hours=1)).replace(tzinfo=utc),
                   pm2_5=3000.0)])

    class Frozen(datetime):
        @classmethod
        def utcnow(cls):
            return NOW
    monkeypatch.setattr(webapp, "datetime", Frozen)
    return TestClient(webapp.create_app(store))


def test_api_areas_matches_snapshot(tmp_path, monkeypatch):
    c = _areas_client(tmp_path, monkeypatch)
    got = {f"{f},{d}": c.get(f"/api/areas?field={f}&days={d}").json()
           for f, d in [("pm2_5", 180), ("voc", 180), ("humidity", 180), ("pm2_5", 20)]}
    got |= {f"series:{r},{f}": c.get(f"/api/areas/series?region={r}&field={f}").json()
            for r, f in [("Kanawha Valley", "pm2_5"), ("Mid-Ohio Valley", "pm2_5"), ("Mid-Ohio Valley", "voc"),
                         ("Nowhere", "pm2_5")]}
    want = _check_snapshot(AREAS_SNAPSHOT, got)
    trends = {a["trend"]["direction"] for r in want.values() for a in r.get("areas", [])}
    assert len(trends) > 1 and any(a["reporting"] == 0 for a in want["pm2_5,180"]["areas"])
    # the spike-only day is gone from the PM series, the mixed day keeps its real reading
    pm = want["series:Mid-Ohio Valley,pm2_5"]
    assert len(pm["points"]) == 30 and pm["points"][-1] == {"date": "2026-07-30", "n": 1, "value": 11.3}
    assert pm["trend"]["direction"] == "falling"
    assert len(want["series:Mid-Ohio Valley,voc"]["points"]) == 31   # only PM is capped


def test_api_query_area_and_window(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    # area alone (no point): a WV region matches community sensors, a county matches the rest
    r = c.get("/api/query?area=Kanawha Valley&param=pm2_5").json()
    assert r["place"] is None and r["filters"]["area"] == "Kanawha Valley"
    assert {s["site_id"] for s in r["sites"]} == {"sensor:197127", "sensor:200001"}
    assert all(s["mi"] is None for s in r["sites"])
    k = c.get("/api/query?area=Kanawha&kind=facility,water_system&limit=500").json()["sites"]
    assert k and all(s["county"] == "Kanawha" for s in k)
    # area with a point narrows the radius search
    near = c.get("/api/query?lat=38.3727&lon=-81.6540&km=8&area=Kanawha Valley").json()["sites"]
    assert {s["kind"] for s in near} == {"sensor"}
    assert c.get("/api/query").status_code == 400
    assert c.get("/api/query?lat=38.3").status_code == 400
    # from/to bound the measurements: latest inside the window, peak inside it too
    base = "/api/query?lat=38.3727&lon=-81.6540&km=8&kind=sensor,sample_site&param=iron,pm2_5"
    full = {s["site_id"]: s["latest"] for s in c.get(base).json()["sites"]}
    assert full["sample_site:S1"]["value"] == 1.9 and full["sensor:197127"]["value"] == 10.04
    early = {s["site_id"]: s["latest"] for s in c.get(f"{base}&to=2026-07-01").json()["sites"]}
    assert early["sample_site:S1"]["value"] == 2.4                 # the 07-02 visit is outside
    assert early["sensor:197127"]["value"] == 10.04                # all three hours are on 07-01
    late = {s["site_id"]: s["latest"] for s in c.get(f"{base}&from=2026-07-02").json()["sites"]}
    assert late["sample_site:S1"]["value"] == 1.9
    assert late["sensor:197127"] is None and late["sensor:200001"] is None
    pk = c.get(f"{base}&since=2026-06-01&to=2026-07-01").json()
    assert {s["site_id"]: s["peak"] and s["peak"]["value"] for s in pk["sites"]}["sample_site:S1"] == 2.4
    assert pk["filters"]["to"] == "2026-07-01"
    assert c.get(f"{base}&from=July").status_code == 400


def test_county_names():
    assert county_names("Marshall County") == ["Marshall"]
    assert county_names("Mc Dowell") == county_names("MCDOWELL") == ["McDowell"]
    assert county_names("Berkeley / Jefferson") == ["Berkeley", "Jefferson"]
    assert county_names("not disclosed") == county_names("") == county_names(None) == []


def test_api_query_county(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    # no point needed; any spelling asks; every spelling in the layers answers
    r = c.get("/api/query?county=kanawha county&kind=facility&limit=500").json()
    assert r["place"] is None and r["filters"]["county"] == ["Kanawha"] and r["count"]
    assert {s["county"] for s in r["sites"]} == {"Kanawha", "Kanawha County"}
    # a comma list, and a project listed under several counties counts in each
    both = c.get("/api/query?county=Kanawha,Mason&kind=data_center&limit=500").json()
    assert both["filters"]["county"] == ["Kanawha", "Mason"]
    assert any("/" in s["county"] for s in both["sites"])
    assert all({"Kanawha", "Mason"} & set(county_names(s["county"])) for s in both["sites"])
    # with a point it narrows the radius search; a region name is not a county
    near = c.get("/api/query?lat=38.3727&lon=-81.6540&km=8&county=Kanawha&kind=facility").json()
    assert near["place"] and all(county_names(s["county"]) == ["Kanawha"] for s in near["sites"])
    assert c.get("/api/query?county=Kanawha Valley").status_code == 400
    assert c.get("/api/query?county=Kanawha,Nowhere").status_code == 400
    # sites with no county listed (sensors) drop out rather than being guessed from a point
    assert c.get("/api/query?county=Kanawha&kind=sensor").json()["sites"] == []


def test_api_query_over(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    base = "/api/query?lat=38.3727&lon=-81.6540&km=8&param=iron,pm2_5"
    ids = lambda r: [s["site_id"] for s in r["sites"]]
    # strictly above, on each site's latest value (iron 1.9, pm2_5 10.04 and 12.35)
    r = c.get(f"{base}&over=10").json()
    assert ids(r) == ["sensor:197127", "sensor:200001"] and r["filters"]["over"] == 10
    assert ids(c.get(f"{base}&over=10.04").json()) == ["sensor:200001"]
    assert "sample_site:S1" in ids(c.get(f"{base}&over=0&kind=sample_site").json())
    # the answer's provenance covers only what's left
    assert set(r["provenance"]) == {"community_sensors"}
    # limit counts sites that pass: the nearest site overall (197127) doesn't
    assert ids(c.get(f"{base}&over=11&limit=1").json()) == ["sensor:200001"]
    # sites that can't have a value never pass, and the from/to window decides "latest"
    assert c.get(f"{base}&over=0&kind=facility").json()["sites"] == []
    assert ids(c.get(f"{base}&over=2&kind=sample_site&to=2026-07-01").json()) == ["sample_site:S1"]
    assert ids(c.get(f"{base}&over=2&kind=sample_site").json()) == []
    # CSV is the same answer
    assert len(c.get(f"{base}&over=10&format=csv").text.strip().splitlines()) == 3
    # needs a param and a real number
    assert c.get("/api/query?lat=38.3727&lon=-81.6540&over=10").status_code == 400
    assert c.get(f"{base}&over=nan").status_code == 400
    assert c.get(f"{base}&over=inf").status_code == 400
    assert c.get(f"{base}&over=lots").status_code == 422
    # series mode: only the rows above it, and the CSV link keeps the filter
    w = c.get("/api/query?site_id=sample_site:S1&param=iron&over=2").json()
    assert [x["value"] for x in w["rows"]] == [2.4] and w["filters"]["over"] == 2
    assert "over=2.0" in w["csv"] and len(c.get(w["csv"]).text.strip().splitlines()) == 2


def test_api_query_series(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    r = c.get("/api/query?site_id=sensor:197127&param=pm2_5").json()
    assert r["mode"] == "series" and r["site"]["name"] == "Kanawha City" and r["site"]["medium"] == "air"
    assert [x["value"] for x in r["rows"]] == [8.04, 9.04, 10.04]          # oldest first
    assert r["rows"][0]["unit"] == "µg/m³" and r["rows"][0]["source"] == "purpleair"
    assert r["count"] == 3 and r["truncated"] is False
    assert r["csv"] == "/api/query?site_id=sensor%3A197127&param=pm2_5&limit=5000&format=csv"
    # water: from/to bound the rows (inclusive dates); limit keeps the newest
    w = "/api/query?site_id=sample_site:S1&param=iron"
    assert [x["value"] for x in c.get(w).json()["rows"]] == [2.4, 1.9]
    assert [x["value"] for x in c.get(f"{w}&to=2026-07-01").json()["rows"]] == [2.4]
    win = c.get(f"{w}&from=2026-07-02&to=2026-07-02").json()
    assert [x["value"] for x in win["rows"]] == [1.9] and win["filters"]["from"] == "2026-07-02"
    assert "from=2026-07-02&to=2026-07-02" in win["csv"]
    one = c.get(f"{w}&limit=1").json()
    assert [x["value"] for x in one["rows"]] == [1.9] and one["truncated"] is True
    # the CSV link returns the same rows
    csv = c.get(r["csv"])
    assert csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.strip().splitlines()
    assert lines[0] == "site_id,name,medium,parameter,ts,value,unit,source,quality"
    assert len(lines) == 4 and lines[1].startswith("sensor:197127,Kanawha City,air,pm2_5,")
    # a parameter the site never measured is empty; one param only; unknown sites are 404
    assert c.get("/api/query?site_id=sensor:200001&param=voc").json()["rows"] == []
    assert c.get("/api/query?site_id=sensor:197127").status_code == 400
    assert c.get("/api/query?site_id=sensor:197127&param=pm2_5,voc").status_code == 400
    assert c.get("/api/query?site_id=sensor:nope&param=pm2_5").status_code == 404
    assert c.get("/api/query?site_id=sensor:197127&param=pm2_5&from=July").status_code == 400


def test_site_pages_for_measured_kinds(tmp_path, monkeypatch):
    c, store = _client(tmp_path, monkeypatch)
    store.save_readings([Reading(source="epa_airdata", sensor_id="54-003-0003",
                                 ts=datetime(2024, 5, d, tzinfo=timezone.utc), pm2_5=7.0 + d)
                         for d in (1, 2)])
    # a sensor: PM2.5 by default, its other parameters as links, the series-mode window
    p = c.get("/site/sensor/197127")
    assert p.status_code == 200 and "Kanawha City" in p.text and "Recent readings" in p.text
    api = "/api/query?site_id=sensor%3A197127&amp;param=pm2_5&amp;from=2026-06-24&amp;to=2026-07-01"
    assert f'data-api="{api}"' in p.text and f'href="{api}&amp;format=csv"' in p.text
    assert 'href="?param=voc"' in p.text and "/static/site.js" in p.text
    # the page's URL is the series mode, so the chart, CSV and API link show the same rows
    rows = c.get(api.replace("&amp;", "&")).json()["rows"]
    assert [r["value"] for r in rows] == [8.04, 9.04, 10.04]
    assert "param=voc" in c.get("/site/sensor/197127?param=voc").text.split("data-api")[1][:80]
    assert "param=pm2_5" in c.get("/site/sensor/197127?param=nope").text.split("data-api")[1][:80]
    # a sample site charts the year up to its latest visit; a gauge its last week
    s = c.get("/site/sample_site/S1?param=iron").text
    assert "Elk River at Clendenin" in s and "from=2025-07-02&amp;to=2026-07-02" in s
    g = c.get("/site/gauge/03198000").text
    assert "Kanawha River at Charleston" in g and "param=conductance" in g and "USGS" in g
    # a monitor reads its AirData history; one with nothing stored says so
    m = c.get("/site/reference_monitor/54-003-0003").text
    assert "MARTINSBURG BALL FIELD" in m and "from=2024-04-02&amp;to=2024-05-02" in m
    empty = c.get("/site/reference_monitor/54-009-0005").text
    assert "No readings stored" in empty and "data-api" not in empty
    assert c.get("/site/sensor/nope").status_code == 404
    assert c.get("/site/gauge/S1").status_code == 404


def test_site_pages_for_permits(tmp_path, monkeypatch):
    c, _ = _client(tmp_path, monkeypatch)
    # a coal discharge on an impaired stream: the 303(d) causes and the caveat, in plain words
    p = c.get("/site/permit/WV1012487")
    assert p.status_code == 200 and "Nicholas Contura" in p.text and "Active discharge permit" in p.text
    assert "Aluminum (trout), Biological, Iron, Selenium, Fecal coliform" in p.text
    assert "Austin Hollow" in p.text and "does not mean this permit caused" in p.text
    assert "effluent-charts/WV1012487" in p.text and "/api/coal-npdes" in p.text
    # a mining permit and an oil & gas permit: the stage, with DEP's own status word
    m = c.get("/site/permit/S100495").text
    assert "Chaplin Hill No. 2" in m and "New / not yet started" in m and '"New"' in m
    assert "Issued 1995-08-04" in m and "no 303(d) record" in m
    g = c.get("/site/permit/051-02423")
    assert "Approved (permit issued)" in g.text and '"Future Use"' in g.text
    # the full record lists every field and each record once (this API number has two rows)
    adv = c.get("/site/permit/051-02423?view=adv").text
    assert "Point Pleasant" in adv and "Full record" in adv
    assert adv.count("Permit stage</th>") == 1
    assert "Impaired stream (303(d)) causes" in c.get("/site/permit/WV1012487?view=adv").text
    assert c.get("/site/permit/nope").status_code == 404


def test_site_pages_have_related_nearby(tmp_path, monkeypatch):
    # site.js fills #site-related from /api/query, one kind at a time (not this page's kind)
    c, _ = _client(tmp_path, monkeypatch)
    p = c.get("/site/sensor/197127").text
    assert "Related nearby" in p and 'id="site-related" data-kind="sensor" data-site="sensor:197127"' in p
    box = p.split('id="site-related"')[1].split(">")[0]
    lat, lon = (box.split(f'data-{k}="')[1].split('"')[0] for k in ("lat", "lon"))
    near = c.get(f"/api/query?lat={lat}&lon={lon}&km=40.25&kind=gauge&limit=1").json()["sites"]
    assert [s["kind"] for s in near] == ["gauge"] and near[0]["mi"] is not None


def test_map_and_list_rows_link_to_working_site_pages(tmp_path, monkeypatch):
    # the ids the popups and lists build /site/<kind>/<id> from (static/*.js, state.js) open a page
    from urllib.parse import quote

    c, _ = _client(tmp_path, monkeypatch)
    sensors = {s["sensor_id"]: s for s in c.get("/api/sensors").json()}
    assert sensors["197127"]["kind"] == "community" and c.get("/site/sensor/197127").status_code == 200
    # AirNow rows carry the 9-digit AQSID; the page finds the monitor under its dashed AQS id
    assert sensors["540390020"]["kind"] == "reference"
    assert c.get("/site/reference_monitor/540390020").status_code == 200
    water = {s["site_id"]: s["kind"] for s in c.get("/api/water/sites").json()["sites"]}
    assert water["03198000"] == "gauge" and water["S1"] == "sample_site"
    for sid, kind in water.items():
        assert c.get(f"/site/{kind}/{sid}").status_code == 200, sid
    for api, key, kind, ref in [("/api/facilities", "facilities", "facility", "registry_id"),
                                ("/api/dep-permits", "permits", "permit", "permit_id"),
                                ("/api/dep-mining", "mines", "permit", "permit_id"),
                                ("/api/coal-npdes", "permits", "permit", "permit"),
                                ("/api/abandoned-wells", "wells", "well", "id"),
                                ("/api/sdwa", "systems", "water_system", "pws_id"),
                                ("/api/nrc-spills", "spills", "spill_report", "report_id")]:
        rows = c.get(api).json()[key]
        assert rows, api
        for row in rows[:3]:
            assert c.get(f"/site/{kind}/{quote(str(row[ref]), safe='')}").status_code == 200, (api, row[ref])
