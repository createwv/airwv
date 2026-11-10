"""Nightly public dumps (airwv/dumps.py) and their /data/dumps/<name> route."""

import csv
import gzip
import io
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from airwv import dumps
from airwv.sources.base import Reading
from airwv.storage import Store
from airwv.web import app as webapp

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _store(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'd.sqlite'}")
    store.create_schema()
    store.save_readings([Reading(source="purpleair", sensor_id="197127", ts=T0, pm2_5=8.0, voc=97.0,
                                 raw={"secret_payload": "not dumped"})])
    store.add_water_readings([
        {"source": "wqp", "site_id": "S1", "site_name": "Elk River", "lat": 38.37, "lon": -81.62,
         "ts": T0, "parameter": "iron", "value": 2.4, "unit": "mg/L", "ingested_at": T0}])
    store.add_report(domain="air", category="odor", description="smell", lat=38.123456,
                     lon=-81.654321, contact_email="resident@example.com", ip_hash="deadbeef")
    store.add_event(title="Plant fire", kind="fire", region="Belle", start_ts=T0, sensor_ids=["197127"],
                    sources=[{"label": "WV DEP", "url": "https://dep.wv.gov/"}])
    store.add_event(title="Unreviewed draft", status="draft")
    store.add_event(title="Withdrawn event", status="archived")
    return store


def _rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_write_dumps(tmp_path):
    store, out = _store(tmp_path), tmp_path / "dumps"
    m = dumps.write_dumps(store, out, now=T0)
    assert sorted(p.name for p in out.iterdir()) == sorted(dumps.FILES)   # no .tmp left behind
    assert json.loads((out / "manifest.json").read_text()) == m
    assert m["generated_at"] == "2026-07-01T00:00:00+00:00"

    meas = _rows(out / "measurement.csv.gz")
    assert len(meas) == m["files"]["measurement.csv.gz"]["rows"] == 3   # pm2_5, voc, iron
    assert {(r["medium"], r["parameter"], r["value"]) for r in meas} == {
        ("air", "pm2_5", "8.0"), ("air", "voc", "97.0"), ("water", "iron", "2.4")}
    assert list(meas[0]) == m["files"]["measurement.csv.gz"]["columns"]

    sites = _rows(out / "site.csv.gz")
    assert len(sites) == m["files"]["site.csv.gz"]["rows"]
    assert {"well", "facility", "sample_site"} <= {r["kind"] for r in sites}
    json.loads(next(r for r in sites if r["kind"] == "well")["attrs"])   # attrs stays JSON

    records = _rows(out / "record.csv.gz")
    assert len(records) == m["files"]["record.csv.gz"]["rows"] > 0
    events = _rows(out / "events.csv.gz")   # published only
    assert [e["title"] for e in events] == ["Plant fire"]
    assert m["files"]["events.csv.gz"]["rows"] == 1
    assert list(events[0]) == m["files"]["events.csv.gz"]["columns"]
    assert json.loads(events[0]["sources"])[0]["label"] == "WV DEP"   # citations stay JSON

    # each layer file's provenance block travels with the dump
    assert m["provenance"]["nrc_spills"]["source"]
    assert "provisional" in m["caveats"]

    # nothing private: report contact / ip / exact coordinates, raw provider payloads
    blob = b"".join(gzip.decompress((out / f).read_bytes()) for f in dumps.FILES if f.endswith(".gz"))
    for private in (b"resident@example.com", b"deadbeef", b"38.123456", b"secret_payload",
                    b"Unreviewed draft", b"Withdrawn event"):
        assert private not in blob


def test_dump_route(tmp_path, monkeypatch):
    store, out = _store(tmp_path), tmp_path / "dumps"
    monkeypatch.setenv("AIRWV_DUMPS_DIR", str(out))
    c = TestClient(webapp.create_app(store))
    assert c.get("/data/dumps/manifest.json").status_code == 404   # before the first run
    dumps.write_dumps(store, now=T0)
    r = c.get("/data/dumps/manifest.json")
    assert r.status_code == 200 and r.json()["generated_at"].startswith("2026-07-01")
    r = c.get("/data/dumps/measurement.csv.gz")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert list(csv.reader(io.StringIO(gzip.decompress(r.content).decode())))[0][0] == "medium"
    for bad in ("d.sqlite", "..%2Fd.sqlite", "site.csv.gz.tmp"):
        assert c.get(f"/data/dumps/{bad}").status_code == 404
    assert c.get("/data/dumps/events.csv.gz").status_code == 200
    page = c.get("/data").text
    assert 'href="/data/dumps/site.csv.gz"' in page and 'href="/data/dumps/events.csv.gz"' in page


def test_parquet_dumps(tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    store, out = _store(tmp_path), tmp_path / "dumps"
    m = dumps.write_dumps(store, out, now=T0)
    for name in dumps.TABLES:   # same rows and columns as the CSV
        t = pq.read_table(out / f"{name}.parquet")
        assert t.column_names == m["files"][f"{name}.parquet"]["columns"]
        assert t.num_rows == m["files"][f"{name}.parquet"]["rows"] == len(_rows(out / f"{name}.csv.gz"))
    meas = pq.read_table(out / "measurement.parquet")
    assert str(meas.schema.field("value").type) == "double"
    assert sorted(meas.column("value").to_pylist()) == [2.4, 8.0, 97.0]
    assert str(pq.read_table(out / "site.parquet").schema.field("lat").type) == "double"
    events = pq.read_table(out / "events.parquet").to_pylist()
    assert [e["title"] for e in events] == ["Plant fire"] and isinstance(events[0]["id"], int)
    assert json.loads(events[0]["sources"])[0]["label"] == "WV DEP"   # citations stay JSON
    texts = "".join(str(pq.read_table(out / f"{n}.parquet").to_pylist()) for n in dumps.TABLES)
    for private in ("resident@example.com", "deadbeef", "38.123456", "secret_payload",
                    "Unreviewed draft", "Withdrawn event"):
        assert private not in texts


def test_dumps_without_pyarrow(tmp_path, monkeypatch):
    monkeypatch.setattr(dumps, "_pyarrow", lambda: None)   # the app and CSVs don't need the extra
    store, out = _store(tmp_path), tmp_path / "dumps"
    m = dumps.write_dumps(store, out, now=T0)
    assert sorted(p.name for p in out.iterdir()) == sorted(
        f for f in dumps.FILES if not f.endswith(".parquet"))
    assert not any(f.endswith(".parquet") for f in m["files"])


def test_parquet_route(tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    store, out = _store(tmp_path), tmp_path / "dumps"
    monkeypatch.setenv("AIRWV_DUMPS_DIR", str(out))
    c = TestClient(webapp.create_app(store))
    assert c.get("/data/dumps/site.parquet").status_code == 404   # before the first run
    dumps.write_dumps(store, now=T0)
    r = c.get("/data/dumps/site.parquet")
    assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.apache.parquet"
    assert r.content[:4] == b"PAR1"
