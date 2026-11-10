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


def test_data_page_shows_as_of_and_sizes(tmp_path, monkeypatch):
    store, out = _store(tmp_path), tmp_path / "dumps"
    monkeypatch.setenv("AIRWV_DUMPS_DIR", str(out))
    c = TestClient(webapp.create_app(store))
    r = c.get("/data")   # before the first run: no date, no sizes, no error
    assert r.status_code == 200 and "These files are as of" not in r.text and "well;" not in r.text

    m = dumps.write_dumps(store, now=T0)
    page = c.get("/data").text
    assert "These files are as of Jun 30, 2026, 8:00 PM ET." in page   # T0 is midnight UTC
    for info in m["files"].values():
        assert f"; {dumps.human_size(info['bytes'])})" in page

    (out / "manifest.json").write_text("{not json")   # a bad manifest never takes the page down
    r = c.get("/data")
    assert r.status_code == 200 and "These files are as of" not in r.text


def test_human_size():
    assert [dumps.human_size(n) for n in (0, 999, 1000, 41_900, 999_499, 999_500, 2_345_678, 3_100_000_000)] == [
        "0 bytes", "999 bytes", "1 KB", "42 KB", "999 KB", "1.0 MB", "2.3 MB", "3.1 GB"]
