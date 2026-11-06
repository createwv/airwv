"""Nightly public dumps (airwv/dumps.py) and their /data/dumps/<name> route."""

import csv
import gzip
import io
import json
from datetime import datetime, timezone

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
    # each layer file's provenance block travels with the dump
    assert m["provenance"]["nrc_spills"]["source"]
    assert "provisional" in m["caveats"]

    # nothing private: report contact / ip / exact coordinates, raw provider payloads
    blob = b"".join(gzip.decompress((out / f).read_bytes()) for f in dumps.FILES if f.endswith(".gz"))
    for private in (b"resident@example.com", b"deadbeef", b"38.123456", b"secret_payload"):
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
    assert 'href="/data/dumps/site.csv.gz"' in c.get("/data").text
