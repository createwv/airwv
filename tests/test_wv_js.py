"""static/wv.js: WV map bounds, /air's default sensor and the time-series y-axis clamp.
Runs the browser helpers under node (skipped if node isn't on PATH)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WV_JS = Path(__file__).resolve().parents[1] / "src/airwv/web/static/wv.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _js(expr: str):
    src = f"const W = require({json.dumps(str(WV_JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", src], text=True, capture_output=True, check=True).stdout
    return json.loads(out)


SENSORS = [
    {"sensor_id": "ref", "name": "AirNow X", "kind": "reference", "lat": 38.35, "lon": -81.63,
     "latest_pm2_5": 90, "last_ts": "2026-11-06T12:00:00+00:00"},
    {"sensor_id": "a", "name": "Charleston A", "kind": "community", "lat": 38.35, "lon": -81.63,
     "latest_pm2_5": 8.0, "last_ts": "2026-11-06T12:00:00+00:00"},
    {"sensor_id": "b", "name": "Morgantown B", "kind": "community", "lat": 39.63, "lon": -79.96,
     "latest_pm2_5": 21.4, "last_ts": "2026-11-06T11:30:00+00:00"},
    {"sensor_id": "stale", "name": "Old C", "kind": "community", "lat": 37.8, "lon": -81.2,
     "latest_pm2_5": 300.0, "last_ts": "2026-11-01T00:00:00+00:00"},
    {"sensor_id": "nocoord", "name": "D", "kind": "community", "lat": None, "lon": None,
     "latest_pm2_5": 50.0, "last_ts": "2026-11-06T12:00:00+00:00"},
]


def test_bbox_is_west_virginia():
    (s, w), (n, e) = _js("W.WV_BBOX")
    assert 37 < s < 37.5 and 40.5 < n < 40.8 and -82.8 < w < -82.5 and -77.8 < e < -77.6


def test_fit_wv_uses_the_bbox_not_the_data():
    got = _js("(() => { let a; W.fitWV({fitBounds: (b, o) => (a = [b, o], 'map')}); return a; })()")
    assert got[0] == _js("W.WV_BBOX") and got[1]["padding"]


def test_default_sensor_is_worst_current_community_reading():
    pick = _js(f"W.pickDefaultSensor({json.dumps(SENSORS)})")
    assert pick["sensor"]["sensor_id"] == "b"   # not the reference, the stale one or the unmapped one
    assert "highest PM2.5" in pick["why"] and "21.4" in pick["why"]


def test_default_sensor_is_nearest_when_a_place_is_given():
    pick = _js(f"W.pickDefaultSensor({json.dumps(SENSORS)}, {{lat: 39.6, lon: -79.9, label: 'you'}})")
    assert pick["sensor"]["sensor_id"] == "b" and pick["why"].startswith("closest to you")
    pick = _js(f"W.pickDefaultSensor({json.dumps(SENSORS)}, {{lat: 38.4, lon: -81.6}})")
    assert pick["sensor"]["sensor_id"] == "a"


def test_default_sensor_fallbacks():
    none_live = [dict(s, latest_pm2_5=None) for s in SENSORS]
    pick = _js(f"W.pickDefaultSensor({json.dumps(none_live)})")
    assert pick["sensor"]["kind"] == "community" and "no sensor has a current reading" in pick["why"]
    assert _js("W.pickDefaultSensor([])") is None


def test_clamp_y_cuts_a_lone_spike():
    vals = [5 + (i % 10) for i in range(1000)] + [1800]
    c = _js(f"W.clampY({json.dumps(vals)})")
    assert c["cap"] < 30 and c["above"] == 1 and c["max"] == 1800


def test_clamp_y_leaves_normal_data_alone():
    assert _js(f"W.clampY({json.dumps([5 + (i % 10) for i in range(1000)])})") is None
    assert _js("W.clampY([1, 2, 900])") is None   # too few points to judge


def test_accessible_markers_name_and_activate_titled_markers():
    # A fake Leaflet: one layer that records its handlers and the attributes set on its element.
    got = _js("""(() => {
      class Marker {}; class CircleMarker {};
      const hooks = [], clicks = [];
      const cls = {addInitHook: f => hooks.push(f)};
      Object.assign(Marker, cls); Object.assign(CircleMarker, cls);
      W.accessibleMarkers({Marker, CircleMarker});
      const attrs = {}, on = {};
      const el = {setAttribute: (k, v) => { attrs[k] = v; }, addEventListener() {}, isConnected: true};
      const m = Object.assign(new CircleMarker(), {options: {title: 'Charleston A, community sensor'},
        on: (t, f) => { on[t] = f; }, getElement: () => el, fire: t => clicks.push(t),
        _map: {getContainer: () => null}, closePopup: () => clicks.push('close')});
      hooks[0].call(m);
      on.add();
      const key = k => on.keydown({originalEvent: {key: k, target: el, preventDefault() {}}});
      key('Enter'); key(' '); key('Escape'); key('a');
      const bare = Object.assign(new Marker(), {options: {}, on: () => clicks.push('wired')});
      hooks[0].call(bare);
      return {attrs, clicks};
    })()""")
    assert got["attrs"] == {"tabindex": "0", "role": "button", "aria-label": "Charleston A, community sensor"}
    assert got["clicks"] == ["click", "click", "close"]   # and a marker with no title isn't touched


def _contrast_on_white(hex_: str) -> float:
    def lum(c):
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 1.05 / (0.2126 * lum(r) + 0.7152 * lum(g) + 0.0722 * lum(b) + 0.05)


def test_ink_darkens_status_colors_to_aa():
    # AQI yellow/orange, ECHO orange, DEP blue, r-value green: too light for text on white
    for c in ["#ffff00", "#ff7e00", "#e07b00", "#2c7fb8", "#1b9e77"]:
        out = _js(f"W.ink('{c}')")
        assert out != c and _contrast_on_white(out) >= 4.5, (c, out)


def test_ink_leaves_dark_colors_and_non_hex_alone():
    assert _js("W.ink('#137333')") == "#137333"
    assert _js("W.ink('#C0392B')") == "#C0392B"
    assert _js("W.ink('red')") == "red"
    assert _js("W.ink(undefined) === undefined") is True
