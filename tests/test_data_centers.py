"""Shape checks for the hand-researched data-center list (data_centers.json).

"not disclosed" / "none found" are real values: a blank or null text field means
someone skipped a question, so these tests fail on it. Only lat/lon may be null
(most sites have no published coordinates). See docs/DATA-CENTERS.md.
"""

import json

from airwv.storage.readmodel import DATA_DIR

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
