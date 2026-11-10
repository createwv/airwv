"""static/site.js: the recent-readings chart on /site/<kind>/<ref> (sensors, monitors,
gauges, sample sites). Runs its pure helpers under node (skipped if node isn't on PATH)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SITE_JS = Path(__file__).resolve().parents[1] / "src/airwv/web/static/site.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
ROWS = [{"ts": "2026-07-01 00:00:00.000000", "value": 8.04, "unit": "µg/m³"},
        {"ts": "2026-07-01 02:00:00.000000", "value": 10.04, "unit": "µg/m³"}]


def _js(expr: str):
    src = (f"const S = require({json.dumps(str(SITE_JS))}); const X = {json.dumps(ROWS)};"
           f" console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([NODE, "-e", src], text=True, capture_output=True, check=True).stdout)


def test_trace_plots_the_rows_in_order():
    t = _js("S.trace(X, 'PM2.5')")
    assert t["x"] == [r["ts"] for r in ROWS] and t["y"] == [8.04, 10.04]
    assert t["name"] == "PM2.5" and t["mode"] == "lines+markers"


NEAR = [[{"site_id": "sensor:2", "kind": "sensor", "ref": "2", "name": "Other sensor", "mi": 0.5}],
        [{"site_id": "well:W1", "kind": "well", "ref": "W1", "name": "Well <1>", "mi": 3.2,
          "county": "Kanawha", "url": "https://example.org/w1"}],
        [{"site_id": "gauge:G", "kind": "gauge", "ref": "G 1", "name": "Elk River", "mi": 1.25},
         {"site_id": "gauge:H", "kind": "gauge", "ref": "H", "name": "Farther gauge", "mi": 2.0}],
        [{"site_id": "permit:P", "kind": "permit", "ref": "P", "name": "No url", "mi": 12.4}],
        [{"site_id": "facility:F", "kind": "facility", "ref": "F", "name": "F", "mi": 4.0},
         {"site_id": "spill_report:S", "kind": "spill_report", "ref": "S", "name": "S", "mi": 5.0},
         {"site_id": "water_system:X", "kind": "water_system", "ref": "X", "name": "X", "mi": None}]]


def test_related_is_the_nearest_of_each_other_kind():
    got = _js(f"S.pickRelated({json.dumps(NEAR)}, 'sensor', 'sensor:1')")
    # sensors are this page's kind; one per kind, closest first, 5 at most, no distance = skip
    assert [s["site_id"] for s in got] == ["gauge:G", "well:W1", "facility:F", "spill_report:S", "permit:P"]
    assert [s["site_id"] for s in _js(f"S.pickRelated({json.dumps(NEAR)}, 'well', 'well:W1', 2)")] \
        == ["sensor:2", "gauge:G"]


def test_related_links_each_site_to_its_page():
    html = _js(f"S.relatedHtml(S.pickRelated({json.dumps(NEAR)}, 'sensor', 'sensor:1'))")
    assert '<a href="/site/gauge/G%201">Elk River</a>' in html and "1.3 mi" in html
    assert '<a href="/site/well/W1">Well &lt;1&gt;</a>' in html
    assert '<a href="/site/permit/P">No url</a>' in html and "Kanawha County" in html and "12 mi" in html
    assert "No other kinds" in _js("S.relatedHtml([])")


def test_latest_is_the_last_row():
    assert _js("S.latest(X, 'PM2.5', 'µg/m³')") == "Latest: PM2.5 10.04 µg/m³ at 2026-07-01 02:00 UTC"
    assert _js("S.latest([], 'PM2.5', '')") == ""
