"""static/dc.js: the data-center card (basic view) and full record (view=adv) that /place
and /nearby render from /api/data-centers. Runs under node (skipped if node isn't on PATH)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airwv.storage import Store
from airwv.storage.readmodel import DATA_DIR
from airwv.web.app import create_app

DC_JS = Path(__file__).resolve().parents[1] / "src/airwv/web/static/dc.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
MONARCH = next(s for s in json.loads((DATA_DIR / "data_centers.json").read_text())["sites"]
               if s["id"] == "monarch-compute-campus")


def _js(expr: str, data) -> str:
    src = (f"const D = require({json.dumps(str(DC_JS))}); const X = {json.dumps(data)};"
           f" console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([NODE, "-e", src], text=True, capture_output=True, check=True).stdout)


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    store = Store(f"sqlite:///{tmp_path_factory.mktemp('dc') / 'dc.sqlite'}")
    store.create_schema()
    return TestClient(create_app(store)).get(
        f"/api/data-centers?lat={MONARCH['lat']}&lon={MONARCH['lon']}&km=16&full=true").json()


def test_card_is_the_plain_summary(api):
    html = _js("D.card(X)", api["near"][0])
    assert "Monarch Compute Campus" in html and "Not disclosed or not found" in html
    assert "/site/data_center/monarch-compute-campus?view=adv" in html


def test_record_has_every_field_and_filing(api):
    html = _js("D.record(X)", api["near"][0])
    assert "R14-0042" in html and "Not disclosed" in html and "HB 2014 certification" in html
    for src in MONARCH["sources"]:
        assert src["url"].replace("&", "&amp;") in html
    for k in MONARCH["air_permits"]:
        assert f"Air permits · {k.replace('_', ' ')}" in html


def test_ask_and_dockets_escape(api):
    html = _js("D.ask(X.how_to_ask) + D.dockets(X.dockets)", api)
    assert "PSC" in html and "24-0611-E-T-PW" in html
    assert "<script>" not in _js("D.ask([{where: '<script>', how: 'x', url: null}])", None)
