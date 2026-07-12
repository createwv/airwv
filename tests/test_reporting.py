"""Community-report intake + pre-screen (POST/GET /api/reports, /api/feedback)."""
from fastapi.testclient import TestClient

from airwv.reporting import jitter, screen
from airwv.storage import Store
from airwv.web.app import create_app


def test_screen_publishes_clean_holds_bad():
    trig = {"chemours", "amos"}
    assert screen("odor near the river this morning", "air", None, trig)[0] == "published_unverified"
    assert screen("check http://spam.com", "air", None, trig)[0] == "held"        # link
    assert screen("this is shit", "air", None, trig)[0] == "held"                 # language
    assert screen("smoke from chemours", "air", None, trig)[0] == "held"          # facility named
    assert screen("odor", "violation", None, trig)[0] == "held"                   # violation domain
    assert screen("odor", "air", "DOW", trig)[0] == "held"                        # names an org


def test_jitter_is_deterministic_and_local():
    a = jitter(38.35, -81.63, 42)
    assert a == jitter(38.35, -81.63, 42)          # stable per report id
    assert abs(a[0] - 38.35) < 0.003 and abs(a[1] + 81.63) < 0.003  # ~within a few hundred m


def _client(tmp_path):
    store = Store(f"sqlite:///{tmp_path/'r.sqlite'}")
    store.create_schema()
    return TestClient(create_app(store))


def test_report_intake_and_public_projection(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/reports", json={"domain": "air", "category": "odor",
                                     "description": "chemical smell", "lat": 38.35, "lon": -81.63})
    assert r.status_code == 200 and r.json()["stage"] == "published_unverified"
    assert c.post("/api/reports", json={"domain": "air", "lat": 38.3, "lon": -81.6, "website": "x"}).status_code == 400
    assert c.post("/api/reports", json={"domain": "nope", "lat": 38.3, "lon": -81.6}).status_code == 400
    held = c.post("/api/reports", json={"domain": "violation", "description": "burning", "lat": 38.3, "lon": -81.6})
    assert held.json()["stage"] == "held"

    pub = c.get("/api/reports").json()["results"]
    assert any(x["domain"] == "air" for x in pub)
    assert all(x["stage"] != "held" for x in pub)          # held not public
    assert "contact_email" not in pub[0] and "ip_hash" not in pub[0]  # private fields absent


def test_rate_limit_and_feedback(tmp_path):
    c = _client(tmp_path)
    for i in range(5):
        assert c.post("/api/reports", json={"domain": "air", "lat": 38.3, "lon": -81.6, "description": f"r{i}"}).status_code == 200
    assert c.post("/api/reports", json={"domain": "air", "lat": 38.3, "lon": -81.6}).status_code == 429
    assert c.post("/api/feedback", json={"kind": "bug", "message": "map broken"}).status_code == 200
    assert c.post("/api/feedback", json={"kind": "bug", "message": "x", "website": "bot"}).status_code == 400


def test_admin_gated_and_moderation(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRWV_ADMIN_TOKEN", "secret")
    c = _client(tmp_path)
    c.post("/api/reports", json={"domain": "violation", "description": "burning", "lat": 38.3, "lon": -81.6})
    assert c.get("/api/admin/reports?status=held").status_code == 401       # no token
    hdr = {"X-Admin-Token": "secret"}
    held = c.get("/api/admin/reports?status=held", headers=hdr).json()["results"]
    assert len(held) == 1 and held[0]["ip_hash"] is not None                # admin sees private fields
    rid = held[0]["id"]
    assert c.post(f"/api/admin/reports/{rid}", json={"action": "publish"}, headers=hdr).status_code == 200
    assert any(x["id"] == rid for x in c.get("/api/reports").json()["results"])   # now public
    c.post(f"/api/admin/reports/{rid}", json={"action": "remove"}, headers=hdr)
    assert not any(x["id"] == rid for x in c.get("/api/reports").json()["results"])  # removed


def test_admin_disabled_without_configured_token(tmp_path):
    c = _client(tmp_path)  # no AIRWV_ADMIN_TOKEN set -> admin fails closed
    assert c.get("/api/admin/reports", headers={"X-Admin-Token": "anything"}).status_code == 401
