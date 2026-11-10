"""Shared URL state (static/state.js): served on every page, and parse/merge round-trips.

The JS checks run the real file under Node (skipped when node isn't installed)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airwv.storage import Store
from airwv.web.app import create_app

STATE_JS = Path(__file__).resolve().parents[1] / "src/airwv/web/static/state.js"


def _client(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'web.sqlite'}")
    store.create_schema()
    return TestClient(create_app(store))


def test_state_js_loads_before_page_scripts(tmp_path):
    c = _client(tmp_path)
    assert c.get("/static/state.js").status_code == 200
    for path, script in [("/", "overview.js"), ("/air", "app.js"),
                         ("/water", "water.js"), ("/nearby", "near_me.js")]:
        html = c.get(path).text
        assert "/static/state.js" in html, path
        assert html.index("/static/state.js") < html.index(f"/static/{script}"), path


def _node(expr):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    code = f"const S = require({json.dumps(str(STATE_JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run([node, "-e", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_parse_validates_and_types():
    s = _node("S.parse('?lat=38.35&lon=-81.6&km=8&param=pm2_5&from=2024-06-01&to=bad"
              "&layers=sensors,ref,wells&view=adv&medium=air')")
    assert s == {"lat": 38.35, "lon": -81.6, "km": 8, "label": None, "medium": "air",
                 "param": "pm2_5", "from": "2024-06-01", "to": None,
                 "layers": ["sensors", "ref", "wells"], "view": "adv"}
    bad = _node("S.parse('?lat=999&lon=-81&param=<x>&view=nope&layers=a,<b>')")
    assert bad["lat"] is None and bad["lon"] is None       # a place needs both, in range
    assert bad["param"] is None and bad["view"] is None and bad["layers"] == ["a"]
    assert _node("S.parse('').layers") is None             # absent ≠ empty (page defaults apply)


def test_legacy_nearby_from_is_a_label():
    s = _node("S.parse('?lat=38&lon=-81&from=Rutledge%20sensor')")
    assert s["label"] == "Rutledge sensor" and s["from"] is None


def test_merge_sets_removes_and_keeps_other_keys():
    qs = _node("S.merge('?src=Some%20Plant&param=pm2_5', "
               "{lat: 38.123456, lon: -81.6, layers: ['sensors','wells'], param: null})")
    assert qs == "?lat=38.1235&lon=-81.6000&layers=sensors,wells&src=Some+Plant"
    assert _node("S.merge('?param=ph', {param: ''})") == ""
    # round trip: what merge writes, parse reads back
    back = _node("S.parse(S.merge('', {lat: 38.2, lon: -81.5, km: 16, from: '2024-01-01', view: 'adv'}))")
    assert (back["lat"], back["lon"], back["km"], back["from"], back["view"]) == (38.2, -81.5, 16, "2024-01-01", "adv")
    assert _node("S.href('/nearby', {lat: 38, lon: -81, label: 'A & B'})") == \
        "/nearby?lat=38.0000&lon=-81.0000&label=A+%26+B"


def test_site_links():
    assert _node("S.site('permit', 'WV 1/2')") == "/site/permit/WV%201%2F2"
    assert _node("S.site('well', '')") is None and _node("S.site('well', null)") is None
    assert _node("S.sensorSite({kind: 'reference', sensor_id: '540390020'})") == "/site/reference_monitor/540390020"
    assert _node("S.sensorSite({kind: 'community', sensor_id: '197127'})") == "/site/sensor/197127"
