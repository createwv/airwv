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


def test_latest_is_the_last_row():
    assert _js("S.latest(X, 'PM2.5', 'µg/m³')") == "Latest: PM2.5 10.04 µg/m³ at 2026-07-01 02:00 UTC"
    assert _js("S.latest([], 'PM2.5', '')") == ""
