"""Tests for the dashboard read API (FastAPI TestClient, no server/network)."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from airwv.sources.base import Reading
from airwv.storage import Store
from airwv.web.app import create_app


def _client(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'web.sqlite'}")
    store.create_schema()
    t0 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    store.save_readings([
        Reading(source="purpleair", sensor_id="197127", ts=t0 + timedelta(hours=i),
                pm2_5=10.0 + (i % 5), voc=100.0 + i)
        for i in range(50)
    ])
    return TestClient(create_app(store))


def test_sensors_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/sensors")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["sensor_id"] == "197127"
    assert data[0]["count"] == 50


def test_series_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/series/197127?field=pm2_5")
    assert r.status_code == 200
    assert r.json()["points"]


def test_diurnal_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/diurnal/197127?field=voc")
    assert r.status_code == 200
    assert len(r.json()["hours"]) == 24


def test_events_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/events/197127?field=pm2_5")
    assert r.status_code == 200
    assert "events" in r.json()


def test_series_date_range_filters(tmp_path):
    client = _client(tmp_path)
    full = client.get("/api/series/197127?field=pm2_5").json()["points"]
    # data is all on 2024-06-01/02; restrict to a date with no data
    empty = client.get("/api/series/197127?field=pm2_5&start=2025-01-01").json()["points"]
    assert len(full) > 0
    assert empty == []


def test_trend_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/trend/197127?field=pm2_5&min_days=1")
    assert r.status_code == 200
    body = r.json()
    assert "direction" in body and "watch" in body


def test_compare_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/compare?sensors=197127&field=pm2_5")
    assert r.status_code == 200
    data = r.json()
    assert data["sensors"][0]["sensor_id"] == "197127"
    assert "night_day_ratio" in data["sensors"][0]


def test_sensors_include_color_field(tmp_path):
    data = _client(tmp_path).get("/api/sensors").json()
    assert "color" in data[0]
    assert data[0]["color"].startswith("#")


def test_bad_field_rejected(tmp_path):
    assert _client(tmp_path).get("/api/series/197127?field=nope").status_code == 400


def test_reference_monitors_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/reference-monitors")
    assert r.status_code == 200
    body = r.json()
    assert "monitors" in body
    assert any(m["county"] == "Kanawha" for m in body["monitors"])


def test_sources_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == "documented"
    assert any(s["name"] == "John Amos Power Plant" for s in body["sources"])
    assert body["disclaimer"]


def test_guide_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/guide")
    assert r.status_code == 200
    body = r.json()
    assert body["pm2_5"][0]["label"] == "Good"
    assert "voc" in body and body["voc_note"]


def test_export_csv_endpoint(tmp_path):
    r = _client(tmp_path).get("/api/export/197127.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("ts,source,sensor_id")


def test_index_page(tmp_path):
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "AirWV" in r.text
