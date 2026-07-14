"""Tests for alert evaluation, dedup/quiet hours, and dispatch."""

from datetime import datetime, timedelta

from airwv.alerts import evaluate
from airwv.config import Config
from airwv.ingest import run_alerts, run_subscribe
from airwv.notify.base import LogNotifier, alert_subject
from airwv.sources.base import Reading
from airwv.storage import Store

_NOW = datetime(2024, 6, 1, 14, 0)  # naive UTC → 10am ET (not quiet)


class _Sub:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.active = kw.get("active", True)
        self.channel = kw.get("channel", "log")
        self.target = kw.get("target", "x")
        self.kind = kw.get("kind", "threshold")
        self.sensor_id = kw.get("sensor_id", "A")
        self.field = kw.get("field", "pm2_5")
        self.threshold = kw.get("threshold", 35.0)
        self.min_interval_seconds = kw.get("min_interval_seconds", 21600)
        self.quiet_start = kw.get("quiet_start")
        self.quiet_end = kw.get("quiet_end")
        self.last_notified_at = kw.get("last_notified_at")
        self.center_lat = kw.get("center_lat")
        self.center_lon = kw.get("center_lon")
        self.radius_km = kw.get("radius_km")


class _Trend:
    def __init__(self, direction, pct_change):
        self.direction = direction
        self.pct_change = pct_change


def _reading(pm):
    return Reading(source="purpleair", sensor_id="A", ts=_NOW, pm2_5=pm)


def test_fires_when_over_threshold():
    alerts = evaluate([_Sub(threshold=35)], {"A": _reading(50)}, _NOW)
    assert len(alerts) == 1
    assert alerts[0].value == 50


def test_no_fire_under_threshold():
    assert evaluate([_Sub(threshold=35)], {"A": _reading(10)}, _NOW) == []


def test_area_scoped_alert_respects_radius():
    # Charleston-centered, 10 km radius. Sensor A is ~2 km away (fires); B is ~80 km (skipped).
    sub = _Sub(threshold=35, sensor_id=None, center_lat=38.35, center_lon=-81.63, radius_km=10)
    coords = {"A": (38.36, -81.64), "B": (38.90, -82.30)}
    latest = {"A": _reading(50), "B": _reading(99)}
    alerts = evaluate([sub], latest, _NOW, sensor_coords=coords)
    assert [a.sensor_id for a in alerts] == ["A"]
    # a sensor with unknown coords never fires an area alert
    assert evaluate([sub], {"C": _reading(99)}, _NOW, sensor_coords={}) == []


def test_rate_limited():
    sub = _Sub(threshold=35, last_notified_at=_NOW - timedelta(hours=1), min_interval_seconds=21600)
    assert evaluate([sub], {"A": _reading(50)}, _NOW) == []  # < 6h since last


def test_quiet_hours_suppress():
    # quiet 9-17 local; 10am ET is inside → suppressed
    sub = _Sub(threshold=35, quiet_start=9, quiet_end=17)
    assert evaluate([sub], {"A": _reading(50)}, _NOW) == []


def test_trend_trigger_fires_on_rising():
    sub = _Sub(kind="trend", threshold=20.0)
    trends = {("A", "pm2_5"): _Trend("rising", 50.0)}
    alerts = evaluate([sub], {"A": _reading(5)}, _NOW, trends=trends)
    assert len(alerts) == 1
    assert alerts[0].kind == "trend"
    assert alerts[0].value == 50.0


def test_trend_trigger_no_fire_when_flat_or_small():
    sub = _Sub(kind="trend", threshold=20.0)
    assert evaluate([sub], {"A": _reading(5)}, _NOW, trends={("A", "pm2_5"): _Trend("flat", 0)}) == []
    assert evaluate([sub], {"A": _reading(5)}, _NOW, trends={("A", "pm2_5"): _Trend("rising", 10.0)}) == []


def test_subject_renders():
    alert = evaluate([_Sub(threshold=35)], {"A": _reading(50)}, _NOW)[0]
    assert "sensor A" in alert_subject(alert)
    LogNotifier().send(alert)  # should not raise


def _config(tmp_path):
    return Config(purpleair_api_key="k", database_url=f"sqlite:///{tmp_path / 'a.sqlite'}",
                  poll_interval_seconds=3600, index_cache_path=tmp_path / "m.json")


def test_subscribe_and_dispatch_via_fake(tmp_path):
    config = _config(tmp_path)
    store = Store(config.database_url)
    store.create_schema()
    store.save_readings([Reading(source="purpleair", sensor_id="A", ts=_NOW, pm2_5=99.0)])
    run_subscribe(config, "log", "ops", threshold=35, store=store)

    sent = []

    class Fake(LogNotifier):
        def send(self, alert):
            sent.append(alert)

    alerts = run_alerts(config, send=True, now=_NOW, store=store, notifier_factory=lambda ch: Fake())
    assert len(alerts) == 1 and len(sent) == 1
    # second run within the interval is rate-limited (last_notified recorded)
    assert run_alerts(config, send=True, now=_NOW, store=store, notifier_factory=lambda ch: Fake()) == []


def test_multi_metric_geo_signup_and_group_confirm(tmp_path):
    from fastapi.testclient import TestClient

    from airwv.web.app import create_app

    store = Store(f"sqlite:///{tmp_path / 'w.sqlite'}")
    store.create_schema()
    c = TestClient(create_app(store))
    r = c.post("/api/alerts/subscribe", json={
        "email": "me@x.org", "metrics": ["pm2_5", "voc", "water"], "level": "sensitive",
        "lat": 38.35, "lon": -81.63, "radius_mi": 10, "label": "Charleston", "elapsed_ms": 5000})
    assert r.status_code == 200

    subs = store.list_subscriptions(active_only=False)
    assert {s.field for s in subs} == {"pm2_5", "voc", "water"}     # one row per metric
    assert len({s.token for s in subs}) == 1                        # sharing one token
    assert all(s.center_lat == 38.35 and round(s.radius_km) == 16 for s in subs)  # 10 mi ≈ 16 km
    assert all(not s.active for s in subs)                          # inactive until confirmed
    pm = next(s for s in subs if s.field == "pm2_5")
    assert pm.threshold == 35.0 and next(s for s in subs if s.field == "voc").threshold == 250.0

    # one confirm link activates the whole group
    token = subs[0].token
    assert c.get(f"/alerts/confirm?token={token}").status_code == 200
    assert all(s.active and s.confirmed_at for s in store.list_subscriptions(active_only=False))

    # re-signup updates in place (no duplicate rows) and can change what's watched
    c.post("/api/alerts/subscribe", json={"email": "me@x.org", "metrics": ["pm2_5"],
                                          "level": "unhealthy", "elapsed_ms": 5000})
    again = store.list_subscriptions(active_only=False)
    assert len(again) == 3                                          # still 3 rows, updated not duplicated
    assert next(s for s in again if s.field == "pm2_5").threshold == 55.0

    # one unsubscribe turns the whole group off
    assert c.get(f"/alerts/unsubscribe?token={token}").status_code == 200
    assert all(not s.active for s in store.list_subscriptions(active_only=False))
