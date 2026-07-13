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


def test_mode_pages_render(tmp_path):
    c = _client(tmp_path)
    # Home at /, dashboard at /air, plus content pages — all share the mode nav + modals
    for path, script in [("/", "overview.js"), ("/air", "app.js"),
                         ("/water", "water.js"), ("/field", "field.js"), ("/sources", "sources.js"),
                         ("/events", "events.js"), ("/learn", "reporting.js"),
                         ("/about", "reporting.js"), ("/admin", "admin.js")]:
        r = c.get(path)
        assert r.status_code == 200, path
        assert "modenav" in r.text and script in r.text, path
        assert "reportmodal" in r.text, path        # shared report/feedback modals everywhere
    assert ">Home</a>" in c.get("/").text
    assert "Air Quality Index" in c.get("/learn").text
    assert "Where the data comes from" in c.get("/about").text
    assert c.get("/analysis", follow_redirects=False).status_code == 301  # old link redirects to /air


def test_events_api_and_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRWV_ADMIN_TOKEN", "secret")
    c = _client(tmp_path)
    assert c.get("/api/events").json()["results"] == []          # empty at first
    hdr = {"X-Admin-Token": "secret"}
    r = c.post("/api/admin/events", json={"title": "Test fire", "kind": "fire",
              "captured": True, "sensor_ids": ["197127"], "status": "published"}, headers=hdr)
    assert r.status_code == 200
    pub = c.get("/api/events").json()["results"]
    assert len(pub) == 1 and pub[0]["title"] == "Test fire" and pub[0]["sensor_ids"] == ["197127"]
    assert c.get("/api/admin/events").status_code == 401          # gated
    eid = pub[0]["id"]
    c.post(f"/api/admin/events/{eid}?action=delete", json={"title": "x"}, headers=hdr)
    assert c.get("/api/events").json()["results"] == []


def test_water_api(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/water/sites").json() == {"sites": []}       # empty until ingested
    assert c.get("/water").status_code == 200
    assert c.get("/api/water/series/x?parameter=ph").json()["points"] == []


def test_field_readings(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRWV_ADMIN_TOKEN", "secret")
    c = _client(tmp_path)
    assert c.get("/field").status_code == 200
    assert c.get("/api/field-readings").json()["results"] == []
    body = {"submitter": "J. Doe", "medium": "water", "parameter": "conductivity",
            "value": 850.0, "unit": "µS/cm", "lat": 38.35, "lon": -81.63}
    assert c.post("/api/field-readings", json=body).status_code == 401           # gated
    r = c.post("/api/field-readings", json=body, headers={"X-Admin-Token": "secret"})
    assert r.status_code == 200
    pub = c.get("/api/field-readings").json()["results"]
    assert len(pub) == 1 and pub[0]["parameter"] == "conductivity" and pub[0]["medium"] == "water"
    assert c.get(f"/api/field-readings/{pub[0]['id']}/photo").status_code == 404  # no photo


def test_area_rollups(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/areas?field=pm2_5")
    assert r.status_code == 200
    body = r.json()
    assert body["field"] == "pm2_5" and isinstance(body["areas"], list)
    for a in body["areas"]:                        # shape of each rollup row
        assert {"region", "sensor_count", "reporting", "value", "trend"} <= set(a)
        assert {"direction", "pct_change", "watch"} <= set(a["trend"])
    # ozone is reference-only → not a valid area field
    assert c.get("/api/areas?field=ozone").status_code == 400
    # per-area series returns a (possibly empty) daily-median list + trend
    s = c.get("/api/areas/series?region=Kanawha Valley&field=pm2_5").json()
    assert s["field"] == "pm2_5" and isinstance(s["points"], list) and "trend" in s
    # dashboard wires the widget in
    page = c.get("/air").text
    assert "areas.js" in page and "How's your area doing?" in page


def test_facilities_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/facilities").json()
    assert {"total", "significant_violation", "violation", "compliant"} <= set(r["summary"])
    if r["facilities"]:                            # data file is present in the repo
        f = r["facilities"][0]
        assert {"name", "lat", "lon", "status", "programs", "echo_url"} <= set(f)
        # status filter narrows to only that status
        sv = c.get("/api/facilities?status=significant_violation").json()["facilities"]
        assert all(x["status"] == "significant_violation" for x in sv)
        # program filter narrows to only facilities in that program
        air = c.get("/api/facilities?program=air").json()["facilities"]
        assert all("air" in x["programs"] for x in air)
    # sources page wires the compliance section in
    page = c.get("/sources").text
    assert "Compliance &amp; permits" in page and "fac-table" in page


def test_dep_permits_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/dep-permits").json()
    assert {"total", "requested", "approved", "construction"} <= set(r["summary"])
    if r["permits"]:                               # data file present in the repo
        p = r["permits"][0]
        assert {"operator", "stage", "county", "lat", "lon"} <= set(p)
        req = c.get("/api/dep-permits?stage=requested").json()["permits"]
        assert all(x["stage"] == "requested" for x in req)
        county = p["county"]
        if county:
            byc = c.get(f"/api/dep-permits?county={county}").json()["permits"]
            assert all(x["county"] == county for x in byc)
    # sources page wires the pipeline section in
    page = c.get("/sources").text
    assert "Oil &amp; gas permit pipeline" in page and "dep-table" in page


def test_dep_mining_endpoint(tmp_path):
    c = _client(tmp_path)
    r = c.get("/api/dep-mining").json()
    assert {"total", "new", "active", "inactive", "acres_disturbed"} <= set(r["summary"])
    if r["mines"]:                                 # data file present in the repo
        m = r["mines"][0]
        assert {"operator", "stage", "type", "lat", "lon"} <= set(m)
        act = c.get("/api/dep-mining?stage=active").json()["mines"]
        assert all(x["stage"] == "active" for x in act)
        coal = c.get("/api/dep-mining?kind=coal").json()["mines"]
        assert all("coal" in (x["type"] or "").lower() for x in coal)
    page = c.get("/sources").text
    assert "Mining permits" in page and "mine-table" in page


def test_alert_signup_flow(tmp_path, monkeypatch):
    monkeypatch.delenv("AIRWV_SMTP_HOST", raising=False)  # SMTP off → waitlist path
    monkeypatch.setenv("AIRWV_ADMIN_TOKEN", "secret")
    c = _client(tmp_path)
    assert c.get("/alerts").status_code == 200
    assert "/alerts" in c.get("/air").text                      # nav link present

    # valid sign-up → stored as pending (inactive, unconfirmed) with a token
    r = c.post("/api/alerts/subscribe",
               json={"email": "Jane@Example.com", "level": "sensitive", "elapsed_ms": 5000})
    assert r.status_code == 200 and r.json()["email_sent"] is False
    admin = c.get("/api/admin/subscriptions", headers={"X-Admin-Token": "secret"}).json()
    assert admin["email_delivery"] is False and len(admin["results"]) == 1
    sub = admin["results"][0]
    assert sub["email"] == "jane@example.com" and sub["active"] is False and sub["threshold"] == 35.0

    # abuse guards
    assert c.post("/api/alerts/subscribe", json={"email": "nope", "elapsed_ms": 5000}).status_code == 400
    assert c.post("/api/alerts/subscribe",
                  json={"email": "a@b.co", "website": "x", "elapsed_ms": 5000}).status_code == 400
    assert c.post("/api/alerts/subscribe",
                  json={"email": "a@b.co", "elapsed_ms": 100}).status_code == 400

    # duplicate email+trigger doesn't create a second row
    c.post("/api/alerts/subscribe", json={"email": "jane@example.com", "level": "sensitive", "elapsed_ms": 5000})
    assert len(c.get("/api/admin/subscriptions", headers={"X-Admin-Token": "secret"}).json()["results"]) == 1

    # confirm activates, unsubscribe deactivates (via the token)
    from airwv.storage import Store
    tok = Store(f"sqlite:///{tmp_path / 'web.sqlite'}").list_subscriptions(active_only=False)[0].token
    assert "all set" in c.get(f"/alerts/confirm?token={tok}").text.lower()
    assert c.get("/api/admin/subscriptions", headers={"X-Admin-Token": "secret"}).json()["results"][0]["active"]
    c.get(f"/alerts/unsubscribe?token={tok}")
    assert not c.get("/api/admin/subscriptions", headers={"X-Admin-Token": "secret"}).json()["results"][0]["active"]
    assert "not found" in c.get("/alerts/confirm?token=bogus").text.lower()
