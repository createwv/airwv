"""Shape checks for the hand-researched data-center list (data_centers.json).

"not disclosed" / "none found" are real values: a blank or null text field means
someone skipped a question, so these tests fail on it. Only lat/lon may be null
(most sites have no published coordinates). See docs/DATA-CENTERS.md.
"""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from airwv import data_centers as dcs
from airwv.storage import Store
from airwv.storage.readmodel import DATA_DIR
from airwv.web.app import create_app

DOC = json.loads((DATA_DIR / "data_centers.json").read_text())
SITES = DOC["sites"]
VALUES = DOC["values"]


def _text_fields(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _text_fields(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _text_fields(v, f"{path}[{i}]")
    else:
        yield path, obj


def test_provenance_block():
    for key in ("source", "scope", "disclaimer", "fetched_at"):
        assert DOC[key]


def test_ids_unique():
    ids = [s["id"] for s in SITES]
    assert len(ids) == len(set(ids))


def test_no_blank_values():
    for s in SITES:
        for path, v in _text_fields({k: v for k, v in s.items() if k not in ("lat", "lon")}):
            assert v not in (None, ""), f"{s['id']}{path} is blank; use 'not disclosed' or 'none found'"


def test_coordinates_paired_and_in_wv():
    for s in SITES:
        assert (s["lat"] is None) == (s["lon"] is None), s["id"]
        if s["lat"] is not None:
            assert 37.2 <= s["lat"] <= 40.7 and -82.7 <= s["lon"] <= -77.7, s["id"]


def test_controlled_vocabularies():
    for s in SITES:
        assert s["status"] in VALUES["status"], s["id"]
        assert s["use"], s["id"]
        for u in s["use"]:
            assert u["category"] in VALUES["use"], s["id"]
            assert u["confidence"] in VALUES["confidence"], s["id"]
            assert u["evidence"]
        for src in s["sources"]:
            assert src["kind"] in VALUES["source_kind"], s["id"]


def test_every_site_is_sourced():
    for s in SITES:
        assert s["sources"], f"{s['id']} has no source"
        for src in s["sources"]:
            assert src["url"].startswith("https://"), s["id"]


def test_watch_list_has_every_comment_deadline():
    md = (Path(__file__).parents[1] / "docs" / "DATA-CENTERS.md").read_text()
    watch = md.split("## Watch list", 1)[1].split("\n## ", 1)[0]
    assert re.search(r"Last checked: \d{4}-\d{2}-\d{2}", watch)
    deadlines = [(s["id"], permit, due) for s in SITES
                 for permit, due in re.findall(r"\b(R1\d-\d{4})\b[^)]*comments to (\d{4}-\d{2}-\d{2})",
                                               s.get("air_permits", {}).get("permits", ""))]
    assert ("monarch-compute-campus", "R14-0042", "2026-11-13") in deadlines
    for site, permit, due in deadlines:
        row = next((ln for ln in watch.splitlines() if permit in ln), "")
        assert due in row, f"{site}: {permit} comments due {due} not on the watch list"


# ---- on the site: the data_center kind, /api/data-centers, /site/data_center/<id> ----

MONARCH = next(s for s in SITES if s["id"] == "monarch-compute-campus")


def _client(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'dc.sqlite'}")
    store.create_schema()
    return TestClient(create_app(store))


def test_card_sorts_known_and_not_disclosed():
    c = dcs.card(MONARCH)
    topics = {t["topic"]: t for t in c["topics"]}
    assert list(topics) == ["water", "energy", "noise", "purpose"]
    water = topics["water"]
    assert {"label": "How much water it takes", "value": None} in water["gaps"]   # "not disclosed"
    assert any(k["label"] == "Water source" and "Ohio River" in k["value"] for k in water["known"])
    assert all(not dcs.is_gap(k["value"]) for t in c["topics"] for k in t["known"])
    assert any(k["label"].startswith("Generative AI (high") for k in topics["purpose"]["known"])
    assert c["sources"] == MONARCH["sources"] and c["url"] == "/site/data_center/monarch-compute-campus"


def test_card_keeps_the_note_on_a_qualified_gap():
    dc = {**MONARCH, "air_permits": {"backup_generators": "none found (federal facility)"}}
    energy = next(t for t in dcs.card(dc)["topics"] if t["topic"] == "energy")
    assert {"label": "Backup generators", "value": "none found (federal facility)"} in energy["gaps"]


def test_how_to_ask_covers_psc_dep_and_records():
    where = " ".join(a["where"] for a in dcs.HOW_TO_ASK)
    assert "PSC" in where and "DEP" in where and "records request" in where


def test_api_near_a_place(tmp_path):
    c = _client(tmp_path)
    r = c.get(f"/api/data-centers?lat={MONARCH['lat']}&lon={MONARCH['lon'] + 0.05}&km=16").json()
    assert r["county"] == "Mason" and r["fetched_at"] == DOC["fetched_at"] and r["disclaimer"]
    assert r["near"][0]["id"] == MONARCH["id"] and r["near"][0]["mi"] < 4
    assert all(x["lat"] is None and "Mason" in x["county"] for x in r["in_county"])
    assert r["how_to_ask"] == dcs.HOW_TO_ASK and r["dockets"] == DOC["dockets"]
    # far away: Monarch isn't listed
    far = c.get("/api/data-centers?lat=37.27&lon=-81.22&km=5").json()
    assert all(x["id"] != MONARCH["id"] for x in far["near"] + far["in_county"])
    # no place: the whole layer
    assert len(c.get("/api/data-centers").json()["near"]) == len(SITES)


def test_data_center_is_a_site_kind(tmp_path):
    c = _client(tmp_path)
    q = c.get(f"/api/query?lat={MONARCH['lat']}&lon={MONARCH['lon']}&km=2&kind=data_center").json()
    assert [s["site_id"] for s in q["sites"]] == ["data_center:monarch-compute-campus"]
    assert q["sites"][0]["records"][0]["kind"] == "project_status"
    assert q["sites"][0]["records"][0]["value"] == "under_construction"
    assert q["provenance"]["data_centers"]["fetched_at"] == DOC["fetched_at"]


def test_site_page_basic_and_advanced(tmp_path):
    c = _client(tmp_path)
    basic = c.get("/site/data_center/monarch-compute-campus")
    assert basic.status_code == 200
    assert "Monarch Compute Campus" in basic.text and "Not disclosed or not found" in basic.text
    assert "psc.state.wv.us" in basic.text and "Full record and filings" in basic.text
    adv = c.get("/site/data_center/monarch-compute-campus?view=adv")
    assert adv.status_code == 200
    assert "R14-0042" in adv.text and "24-0611-E-T-PW" in adv.text
    for src in MONARCH["sources"]:
        assert src["url"].replace("&", "&amp;") in adv.text
    # a site with no public coordinates still has a page
    unmapped = next(s for s in SITES if s["lat"] is None)
    page = c.get(f"/site/data_center/{unmapped['id']}")
    assert page.status_code == 200 and "exact location not public" in page.text
    assert c.get("/site/data_center/no-such-site").status_code == 404
    assert c.get("/site/well/anything").status_code == 404   # other kinds come with the site-pages item


def test_api_full_record_for_the_advanced_view(tmp_path):
    c = _client(tmp_path)
    q = f"/api/data-centers?lat={MONARCH['lat']}&lon={MONARCH['lon']}&km=16"
    assert "record" not in c.get(q).json()["near"][0]   # the basic view stays small
    full = c.get(f"{q}&full=true").json()["near"][0]
    assert full["record"]["id"] == MONARCH["id"] and full["record"]["air_permits"] == MONARCH["air_permits"]


def test_place_page_loads_the_data_center_renderer(tmp_path):
    c = _client(tmp_path)
    r = c.get("/place?place=rutledge&km=8&view=adv")
    assert r.status_code == 200 and r.text.index("/static/dc.js") < r.text.index("/static/place.js")
    assert 'id="pl-dc"' in r.text
    assert "/static/dc.js" in c.get("/nearby").text
