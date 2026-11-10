"""static/place.js: the /place status lines link the nearest sensor, monitor, gauge or sample
site to its /site/<kind>/<ref> page. Runs the pure line builders under node (skipped if node
isn't on PATH)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLACE_JS = Path(__file__).resolve().parents[1] / "src/airwv/web/static/place.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
P = {"km": 8}


def _js(expr: str):
    src = (f"const S = require({json.dumps(str(PLACE_JS))}); const P = {json.dumps(P)};"
           f" console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([NODE, "-e", src], text=True, capture_output=True, check=True).stdout)


def _site(kind, ref, name, value, param="pm2_5", ts="2026-07-01 02:00:00"):
    return {"kind": kind, "ref": ref, "name": name, "mi": 1.2,
            "latest": {"ts": ts, "value": value, "parameter": param, "unit": "uS/cm"}}


def test_site_href_only_for_measured_kinds():
    assert _js("S.siteHref({kind: 'sensor', ref: '197127'})") == "/site/sensor/197127"
    assert _js("S.siteHref({kind: 'sample_site', ref: 'WVDEP 1/A'})") == "/site/sample_site/WVDEP%201%2FA"
    assert _js("S.siteHref({kind: 'water_system', ref: 'WV1'})") is None


def test_air_line_links_the_sensor_page():
    air = {"sites": [_site("reference_monitor", "54-039-0020", "Charleston <EPA>", 9.0)]}
    html = _js(f"S.airLine({json.dumps(air)}, P)")
    assert '<a href="/site/reference_monitor/54-039-0020">Charleston &lt;EPA&gt;</a>' in html
    assert '<a href="/site/reference_monitor/54-039-0020">data behind this →</a>' in html
    assert '<a href="/air">statewide map →</a>' in _js("S.airLine({sites: []}, P)")


def test_water_line_links_the_gauge_or_sample_site_page():
    water = {"sites": [_site("sample_site", "S1", "Elk River", 310.0, "conductance")]}
    html = _js(f"S.waterLine({json.dumps(water)}, P)")
    assert 'nearest reading <a href="/site/sample_site/S1">Elk River</a>' in html
    assert '<a href="/site/sample_site/S1">data behind this →</a>' in html
    assert '<a href="/water">data behind this →</a>' in _js("S.waterLine({sites: []}, P)")


def test_place_query_is_what_home_reopens():
    assert _js("S.placeQuery({id: 'rutledge', km: 8})") == "place=rutledge&km=8"
    assert _js("S.placeQuery({id: '', name: 'My home', lat: 38.38, lon: -81.585, km: 16})") \
        == "lat=38.3800&lon=-81.5850&name=My%20home&km=16"
