"""Place-based record alerts: new spills / violations near a subscription's place."""

import json
from datetime import datetime, timedelta

from airwv.alerts import Alert, evaluate, evaluate_records, nearest_county, record_key
from airwv.config import Config
from airwv.ingest import run_alerts
from airwv.notify.base import alert_body, alert_subject
from airwv.storage import Store
from airwv.storage.readmodel import ReadModel

_NOW = datetime(2024, 6, 1, 14, 0)  # naive UTC → 10am ET (not quiet)
CHARLESTON = (38.35, -81.63)


class _Sub:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.active = kw.get("active", True)
        self.channel = kw.get("channel", "log")
        self.target = kw.get("target", "x")
        self.kind = kw.get("kind", "record")
        self.sensor_id = None
        self.field = kw.get("field", "spills")
        self.threshold = 0.0
        self.min_interval_seconds = kw.get("min_interval_seconds", 21600)
        self.quiet_start = kw.get("quiet_start")
        self.quiet_end = kw.get("quiet_end")
        self.last_notified_at = kw.get("last_notified_at")
        self.center_lat = kw.get("center_lat", CHARLESTON[0])
        self.center_lon = kw.get("center_lon", CHARLESTON[1])
        self.radius_km = kw.get("radius_km", 16.0)
        self.label = kw.get("label", "Charleston")
        self.records_checked_at = kw.get("records_checked_at", _NOW - timedelta(days=1))


def _rec(ref, lat, lon, county="Kanawha", rule="spills", kind="spill", value="Fixed",
         county_placed=False, **extra):
    return {"rule": rule, "site_id": f"spill_report:{ref}", "kind": kind, "value": value,
            "effective": extra.get("effective", "2024-05-30"), "url": "https://nrc.uscg.mil/",
            "name": value, "lat": lat, "lon": lon, "county": county,
            "county_placed": county_placed, "source": "National Response Center",
            "attrs": extra.get("attrs", {})}


def test_first_check_takes_a_baseline_and_fires_nothing():
    sub = _Sub(records_checked_at=None)
    recs = [_rec(1, 38.36, -81.64)]
    alerts, baselines, _ = evaluate_records([sub], recs, {}, _NOW)
    assert alerts == [] and baselines == {1: {record_key(recs[0])}}


def test_new_record_in_place_fires_once():
    near, far = _rec(1, 38.36, -81.64), _rec(2, 39.63, -79.95, county="Monongalia")
    alerts, *_ = evaluate_records([_Sub()], [near, far], {1: set()}, _NOW)
    assert len(alerts) == 1 and alerts[0].kind == "record"
    assert [r["site_id"] for r in alerts[0].records] == ["spill_report:1"]   # Morgantown is out
    # once seen, it doesn't fire again
    seen = {1: {record_key(near)}}
    assert evaluate_records([_Sub()], [near, far], seen, _NOW)[0] == []


def test_county_placed_record_matches_the_place_county():
    # on the Kanawha centroid (~5 km out of a 3 km radius) but the place is in Kanawha
    county_only = _rec(3, 38.32, -81.53, county="KANAWHA", county_placed=True)
    exact_far = _rec(4, 38.32, -81.53, county="Kanawha", county_placed=False)
    alerts, *_ = evaluate_records([_Sub(radius_km=3.0)], [county_only, exact_far], {1: set()}, _NOW)
    assert [r["site_id"] for r in alerts[0].records] == ["spill_report:3"]
    assert nearest_county(*CHARLESTON) == "Kanawha"


def test_rule_quiet_hours_and_rate_limit_hold_the_alert():
    rec = _rec(1, 38.36, -81.64)
    viol = _rec(9, 38.36, -81.64, rule="violations", kind="violation", value="violation")
    assert evaluate_records([_Sub(field="violations")], [rec], {1: set()}, _NOW)[0] == []
    assert len(evaluate_records([_Sub(field="violations")], [viol], {1: set()}, _NOW)[0]) == 1
    quiet = _Sub(quiet_start=9, quiet_end=11)
    assert evaluate_records([quiet], [rec], {1: set()}, _NOW)[0] == []
    recent = _Sub(last_notified_at=_NOW - timedelta(hours=1))
    assert evaluate_records([recent], [rec], {1: set()}, _NOW)[0] == []


def test_spill_age_cap_skips_old_reports_only():
    def rec(ref, effective):
        return _rec(ref, 38.36, -81.64, effective=effective)

    edge, old = rec(1, "2024-04-02"), rec(2, "2024-04-01")           # 60 and 61 days before _NOW
    undated, garbled = rec(3, None), rec(4, "unknown")
    alerts, _ = evaluate_records([_Sub()], [edge, old, undated, garbled], {1: set()}, _NOW)
    assert {r["site_id"] for r in alerts[0].records} == {
        "spill_report:1", "spill_report:3", "spill_report:4"}
    # only an old report: nothing to send
    assert evaluate_records([_Sub()], [old], {1: set()}, _NOW)[0] == []
    # the cap is for spills; an old violation still alerts
    viol = _rec(9, 38.36, -81.64, rule="violations", kind="violation", value="violation",
                effective="2020-01-01")
    assert len(evaluate_records([_Sub(field="violations")], [viol], {1: set()}, _NOW)[0]) == 1


def test_email_states_the_spill_age_cap():
    def body(field, rec):
        return alert_body(Alert(subscription_id=1, channel="log", target="x", sensor_id=rec["site_id"],
                                field=field, value=1.0, threshold=0.0, ts=_NOW, kind="record",
                                label="Charleston", records=[rec]))

    assert "leaves out reported spills dated more than 60 days back" in body(
        "spills", _rec(1, 38.36, -81.64))
    assert "days back" not in body(
        "violations", _rec(9, 38.36, -81.64, rule="violations", kind="violation", value="violation"))


def test_waitlist_rows_are_record_subs_and_skip_the_reading_matcher():
    legacy = _Sub(kind="threshold", field="spills")       # how the old waitlist stored them
    assert evaluate([legacy], {}, _NOW) == []
    assert len(evaluate_records([legacy], [_rec(1, 38.36, -81.64)], {1: set()}, _NOW)[0]) == 1


def _viol(ref=9, value="violation"):
    return _rec(ref, 38.36, -81.64, rule="violations", kind="violation", value=value)


def _check(sub, recs, last):
    return evaluate_records([sub], recs, {1: {record_key(r) for r in recs}}, _NOW,
                            last_present={1: last})


def test_returned_violation_alerts_with_when_it_was_last_listed():
    v = _viol()
    sub = _Sub(field="violations", records_checked_at=_NOW - timedelta(hours=1))
    alerts, _, present = _check(sub, [v], {record_key(v): _NOW - timedelta(days=90)})
    assert [r["last_present"] for r in alerts[0].records] == ["2024-03-03"]
    assert present == {1: set()}                             # stamped only once it's sent
    assert "last_present" not in v                           # shared across subscriptions


def test_a_record_that_never_left_or_only_blipped_stays_silent():
    v = _viol()
    sub = _Sub(field="violations", records_checked_at=_NOW - timedelta(hours=1))
    for last in (_NOW - timedelta(hours=1), _NOW - timedelta(days=29), None):
        alerts, _, present = _check(sub, [v], {record_key(v): last})
        assert alerts == [] and present == {1: {record_key(v)}}


def test_our_own_downtime_is_not_a_return():
    v = _viol()                                              # last run 90 days ago, found then
    last = _NOW - timedelta(days=90)
    assert _check(_Sub(field="violations", records_checked_at=last), [v], {record_key(v): last})[0] == []


def test_returned_alert_held_by_quiet_hours_or_rate_limit_is_not_lost():
    v = _viol()
    last = {record_key(v): _NOW - timedelta(days=90)}
    checked = _NOW - timedelta(hours=1)
    for sub in (_Sub(field="violations", records_checked_at=checked, quiet_start=9, quiet_end=11),
                _Sub(field="violations", records_checked_at=checked,
                     last_notified_at=_NOW - timedelta(hours=1))):
        alerts, _, present = _check(sub, [v], last)
        assert alerts == [] and present == {1: set()}


def test_a_spill_that_reappears_is_not_a_return():
    s = _rec(1, 38.36, -81.64)
    sub = _Sub(records_checked_at=_NOW - timedelta(hours=1))
    assert _check(sub, [s], {record_key(s): _NOW - timedelta(days=90)})[0] == []


def test_returned_subject_and_body_say_so_and_dont_claim_a_fix():
    back = {**_viol(1), "last_present": "2024-03-03"}

    def alert(recs):
        return Alert(subscription_id=1, channel="log", target="x", sensor_id="s", field="violations",
                     value=1.0, threshold=0.0, ts=_NOW, kind="record", label="Charleston", records=recs)

    assert alert_subject(alert([back])) == "AirWV: 1 facility violation back on the list near Charleston"
    mixed = alert([_viol(2), back])
    assert alert_subject(mixed) == "AirWV: 1 new and 1 returning facility violations near Charleston"
    body = alert_body(mixed)
    assert "last saw this listed on 2024-03-03" in body and body.count("Back on the list") == 1
    assert "fixed" not in body and "resolved" not in body and "cleared" not in body


def test_message_is_plain_and_sourced():
    rec = _rec(1, 38.36, -81.64, county_placed=True, effective="2024-05-30",
               attrs={"materials": [{"name": "Diesel Fuel"}], "reached_water": True,
                      "body_of_water": "Elk River"})
    a = Alert(subscription_id=1, channel="log", target="x", sensor_id=rec["site_id"],
              field="spills", value=1.0, threshold=0.0, ts=_NOW, kind="record",
              label="Charleston", records=[rec], unsubscribe_url="https://h/alerts/unsubscribe?token=t")
    assert alert_subject(a) == "AirWV: 1 new reported spill near Charleston"
    body = alert_body(a)
    assert "2024-05-30: a spill of Diesel Fuel was reported to the National Response Center" in body
    assert "location approximate" in body and "Elk River" in body
    assert "not findings of harm" in body and "Source: National Response Center" in body
    assert body.rstrip().endswith("Unsubscribe: https://h/alerts/unsubscribe?token=t")


def _spills_file(data_dir, spills):
    (data_dir / "nrc_spills.json").write_text(json.dumps({
        "source": "National Response Center (test)", "fetched_at": "2024-06-01T00:00:00Z",
        "spills": spills}))


def _spill(ref, lat, lon, county="Kanawha"):
    return {"report_id": ref, "date": "2024-05-30", "county": county, "type": "Fixed",
            "materials": [{"name": "Diesel Fuel"}], "reached_water": False,
            "lat": lat, "lon": lon, "geo": "exact"}


def test_run_alerts_baselines_then_fires_on_a_new_spill(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRWV_BASE_URL", "https://air.example")
    data = tmp_path / "data"
    data.mkdir()
    _spills_file(data, [_spill(1, 38.36, -81.64)])
    config = Config(purpleair_api_key="k", database_url=f"sqlite:///{tmp_path / 'a.sqlite'}",
                    poll_interval_seconds=3600, index_cache_path=tmp_path / "m.json")
    store = Store(config.database_url)
    store.create_schema()
    store._read_model = ReadModel(store._engine, data_dir=data)
    sid = store.add_subscription(channel="email", target="me@x.org", kind="record", field="spills",
                                 threshold=0.0, center_lat=CHARLESTON[0], center_lon=CHARLESTON[1],
                                 radius_km=16.0, label="Charleston", token="tok")
    sent = []

    class Fake:
        def send(self, alert):
            sent.append(alert)

    def run(at, send=True):
        return run_alerts(config, send=send, now=at, store=store, notifier_factory=lambda ch: Fake())

    assert run(_NOW, send=False) == []                       # dry run: no baseline stored
    assert store.seen_records([sid]) == {sid: set()}
    assert run(_NOW) == [] and len(store.seen_records([sid])[sid]) == 1   # baseline: spill 1

    # a new spill nearby, and one in Morgantown: only the near one alerts
    _spills_file(data, [_spill(1, 38.36, -81.64), _spill(2, 38.34, -81.62),
                        _spill(3, 39.63, -79.95, county="Monongalia")])
    later = _NOW + timedelta(hours=1)
    alerts = run(later)
    assert [r["site_id"] for a in alerts for r in a.records] == ["spill_report:2"]
    assert sent[0].unsubscribe_url == "https://air.example/alerts/unsubscribe?token=tok"
    assert "Diesel Fuel" in alert_body(sent[0])
    assert run(later + timedelta(days=1)) == []              # seen: no repeat


def _facilities_file(data_dir, status):
    (data_dir / "echo_facilities.json").write_text(json.dumps({
        "source": "EPA ECHO (test)", "fetched_at": "2024-06-01T00:00:00Z",
        "facilities": [{"registry_id": "110", "name": "Acme Plant", "status": status,
                        "county": "Kanawha", "lat": 38.36, "lon": -81.64,
                        "echo_url": "https://echo.epa.gov/x"}]}))


def test_run_alerts_tells_a_returned_violation_from_one_already_seen(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _facilities_file(data, "violation")
    config = Config(purpleair_api_key="k", database_url=f"sqlite:///{tmp_path / 'v.sqlite'}",
                    poll_interval_seconds=3600, index_cache_path=tmp_path / "m.json")
    store = Store(config.database_url)
    store.create_schema()
    store._read_model = ReadModel(store._engine, data_dir=data)
    sid = store.add_subscription(channel="email", target="me@x.org", kind="record",
                                 field="violations", threshold=0.0, center_lat=CHARLESTON[0],
                                 center_lon=CHARLESTON[1], radius_km=16.0, label="Charleston",
                                 token="tok")
    sent = []

    class Fake:
        def send(self, alert):
            sent.append(alert)

    def run(at):
        return run_alerts(config, send=True, now=at, store=store, notifier_factory=lambda ch: Fake())

    assert run(_NOW) == []                                   # baseline
    assert run(_NOW + timedelta(days=10)) == []              # still listed: already seen

    _facilities_file(data, "compliant")                      # drops off the list
    assert run(_NOW + timedelta(days=20)) == []
    assert run(_NOW + timedelta(days=60)) == []

    _facilities_file(data, "violation")                      # listed again, same status
    back = _NOW + timedelta(days=61)
    alerts = run(back)
    assert [r["last_present"] for a in alerts for r in a.records] == ["2024-06-11"]
    assert "back on the list" in alert_subject(sent[0])
    assert run(back + timedelta(days=1)) == []               # told once
    (last,) = store.records_last_present([sid])[sid].values()
    assert last == back + timedelta(days=1)


def test_signup_stores_record_metrics_as_record_kind(tmp_path):
    from fastapi.testclient import TestClient

    from airwv.web.app import create_app

    store = Store(f"sqlite:///{tmp_path / 'w.sqlite'}")
    store.create_schema()
    c = TestClient(create_app(store))
    assert "coming soon" not in c.get("/alerts").text
    r = c.post("/api/alerts/subscribe", json={
        "email": "me@x.org", "metrics": ["pm2_5", "spills", "violations"], "level": "sensitive",
        "lat": 38.35, "lon": -81.63, "radius_mi": 10, "label": "Charleston", "elapsed_ms": 5000})
    assert r.status_code == 200 and "Facility violations near me" in r.json()["watching"]
    kinds = {s.field: s.kind for s in store.list_subscriptions(active_only=False)}
    assert kinds == {"pm2_5": "threshold", "spills": "record", "violations": "record"}


def test_cli_subscribe_scopes_a_record_alert_to_a_place(tmp_path):
    from airwv.ingest import run_subscribe

    config = Config(purpleair_api_key="k", database_url=f"sqlite:///{tmp_path / 'c.sqlite'}",
                    poll_interval_seconds=3600, index_cache_path=tmp_path / "m.json")
    store = Store(config.database_url)
    run_subscribe(config, "webhook", "https://hooks.example/x", 0.0, field="violations",
                  kind="record", store=store, center=CHARLESTON, radius_km=16.0)
    (sub,) = store.list_subscriptions()
    assert (sub.kind, sub.field, sub.center_lat, sub.radius_km) == ("record", "violations", 38.35, 16.0)
    assert sub.records_checked_at is None
