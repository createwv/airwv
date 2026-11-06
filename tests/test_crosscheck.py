"""Tests for the neighbor cross-check and automatic event candidates."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from airwv.analysis.crosscheck import (
    cross_check,
    episodes,
    event_candidates,
    km_between,
    matching_event,
)
from airwv.analysis.events import detrend_events, residual_scores
from airwv.ingest import run_candidates
from airwv.sources.base import Reading
from airwv.storage import Store

_T0 = datetime(2024, 6, 1)
# Three sensors a few km apart (Parkersburg-ish) and one 100+ km away.
COORDS = {"a": (39.27, -81.54), "b": (39.29, -81.54), "c": (39.33, -81.55), "far": (38.35, -81.63)}


def _series(sid, spikes=(), n=200, source="purpleair", lat=None, lon=None):
    values = [10.0 + (i % 3) for i in range(n)]
    for i, v in spikes:
        values[i] = v
    return [Reading(source=source, sensor_id=sid, ts=_T0 + timedelta(hours=i), pm2_5=v, lat=lat, lon=lon)
            for i, v in enumerate(values)]


def _run(series_by_sensor, **kw):
    flagged, scores = [], {}
    for sid, rows in series_by_sensor.items():
        scores[sid] = residual_scores(rows, tzname="UTC")
        flagged.extend(detrend_events(rows, tzname="UTC", z_threshold=6))
    checks = cross_check(episodes(flagged), scores, COORDS, **kw)
    return checks, event_candidates(checks, COORDS)


def test_km_between():
    assert round(km_between(COORDS["a"], COORDS["b"]), 1) == 2.2
    assert km_between(COORDS["a"], COORDS["far"]) > 100


def test_episodes_collapse_consecutive_readings():
    rows = _series("a", spikes=[(150, 300.0), (151, 400.0), (152, 250.0), (180, 300.0)])
    eps = episodes(detrend_events(rows, tzname="UTC", z_threshold=6))
    assert len(eps) == 2
    assert eps[0].n == 3 and eps[0].peak.value == 400.0
    assert eps[0].start == _T0 + timedelta(hours=150) and eps[0].end == _T0 + timedelta(hours=152)


def test_shared_spike_is_corroborated_and_proposed():
    checks, cands = _run({
        "a": _series("a", spikes=[(150, 300.0)]),
        "b": _series("b", spikes=[(151, 120.0)]),   # an hour later, still in the window
        "c": _series("c"),                           # neighbor reporting, flat
    })
    by = {c.episode.sensor_id: c for c in checks}
    assert by["a"].status == "corroborated" and by["a"].agree == ["b"] and by["a"].neighbors == ["b", "c"]
    assert len(cands) == 1
    cand = cands[0]
    assert cand.sensor_ids == ["a", "b"] and cand.peak_sensor == "a" and cand.peak_value == 300.0
    assert cand.start == _T0 + timedelta(hours=150) and cand.end == _T0 + timedelta(hours=151)
    assert 39.27 <= cand.lat <= 39.29


def test_lone_spike_with_flat_neighbors_is_single_sensor():
    checks, cands = _run({"a": _series("a", spikes=[(150, 800.0)]), "b": _series("b"), "c": _series("c")})
    assert [c.status for c in checks] == ["single_sensor"]
    assert checks[0].peak_z["b"] < 3
    assert cands == []


def test_far_sensor_is_not_a_neighbor():
    checks, cands = _run({"a": _series("a", spikes=[(150, 300.0)]), "far": _series("far", spikes=[(150, 300.0)])})
    assert {c.status for c in checks} == {"no_neighbors"}
    assert cands == []


def test_neighbor_without_data_in_window_does_not_count():
    # b only reports for the first 100 hours: it can't vouch either way for hour 150.
    checks, _ = _run({"a": _series("a", spikes=[(150, 300.0)]), "b": _series("b", n=100)})
    assert checks[0].status == "no_neighbors" and checks[0].neighbors == []


def test_spike_outside_window_does_not_corroborate():
    checks, cands = _run({"a": _series("a", spikes=[(150, 300.0)]), "b": _series("b", spikes=[(160, 300.0)])})
    assert {c.status for c in checks} == {"single_sensor"}
    assert cands == []


def test_matching_event_by_sensor_or_distance():
    checks, cands = _run({"a": _series("a", spikes=[(150, 300.0)]), "b": _series("b", spikes=[(150, 200.0)])})
    cand = cands[0]
    when = _T0 + timedelta(hours=140)
    by_sensor = SimpleNamespace(id=1, start_ts=when, end_ts=when + timedelta(hours=5),
                                sensor_ids=["b"], lat=None, lon=None)
    nearby = SimpleNamespace(id=2, start_ts=when.replace(tzinfo=timezone.utc), end_ts=None,
                             sensor_ids=[], lat=39.25, lon=-81.56)
    elsewhere = SimpleNamespace(id=3, start_ts=when, end_ts=None, sensor_ids=[], lat=38.35, lon=-81.63)
    other_month = SimpleNamespace(id=4, start_ts=when + timedelta(days=30), end_ts=None,
                                  sensor_ids=["a"], lat=39.27, lon=-81.54)
    assert matching_event(cand, [by_sensor]).id == 1
    assert matching_event(cand, [nearby]).id == 2
    assert matching_event(cand, [elsewhere, other_month]) is None


def test_run_candidates_over_store(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'cc.sqlite'}")
    store.create_schema()
    store.save_readings(_series("a", spikes=[(150, 300.0)]) + _series("b", spikes=[(150, 200.0)])
                        + _series("c")
                        # AirNow monitor carries its own coords on the readings
                        + _series("m1", spikes=[(151, 90.0)], source="airnow", lat=39.28, lon=-81.53))
    coords = {k: v for k, v in COORDS.items() if k != "far"}
    r = run_candidates(store=store, coords=coords, days=30)
    assert r["until"] == _T0 + timedelta(hours=199)
    assert len(r["candidates"]) == 1
    assert r["candidates"][0].sensor_ids == ["a", "b", "m1"]
    # an explicit window that misses the spike finds nothing
    r = run_candidates(store=store, coords=coords, start=_T0, end=_T0 + timedelta(hours=120))
    assert r["candidates"] == []
