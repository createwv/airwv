"""Product smoke suite: the key pages at phone (390) and laptop (1280) widths, as a user sees
them. One harness run per session (conftest.py); screenshots + report.md land in
tests/product/out/. Later plan items cite these checks; known gaps are xfail with the plan
item that should close them, so they turn into XPASS (not red) when it lands.
"""

import pytest
from conftest import PAGES, VIEWPORTS  # tests/ isn't a package; pytest puts this dir on sys.path

# West Virginia plus a margin for border sensors (matches the app's region box in spirit).
WV = {"south": 37.0, "north": 40.8, "west": -82.8, "east": -77.5}
NAV_TARGET = 6   # six top-level items, "More" included (docs/DIRECTION.md §3)

CASES = [(name, w) for w, _ in VIEWPORTS for name, _ in PAGES]
IDS = [f"{n}-{w}" for n, w in CASES]


def _get(product, name, width):
    return next(r for r in product["results"] if r["page"] == name and r["width"] == width)


@pytest.mark.parametrize("name,width", CASES, ids=IDS)
def test_page_loads(product, name, width):
    r = _get(product, name, width)
    assert r["error"] is None, r["error"]
    assert r["status"] == 200
    assert r["failed"] == [], f"local requests failed: {r['failed']}"


@pytest.mark.parametrize("name,width", CASES, ids=IDS)
def test_no_console_errors(product, name, width):
    r = _get(product, name, width)
    if name == "water" and product["db"] == "seeded fake DB" and r["pageErrors"]:
        pytest.xfail("water.js reads WATER_PARAMS[undefined].label when there are no water sites")
    assert r["pageErrors"] == [], r["pageErrors"]
    assert r["consoleErrors"] == [], r["consoleErrors"]


@pytest.mark.parametrize("name,width", CASES, ids=IDS)
def test_nav_present(product, name, width):
    assert _get(product, name, width).get("navCount", 0) > 0


@pytest.mark.parametrize("width", [w for w, _ in VIEWPORTS])
def test_nav_count(product, width):
    assert _get(product, "home", width)["navCount"] <= NAV_TARGET


@pytest.mark.parametrize("width", [
    pytest.param(390, marks=pytest.mark.xfail(
        reason="plan: 'Place picker + my place now' — at 390 the hero fills the first screen",
        strict=False)),
    1280,
])
def test_home_reading_above_fold(product, width):
    """Is my air OK? Home shows a current reading (number + unit) on the first screen."""
    r = _get(product, "home", width)
    assert r.get("reading"), (f"no reading on the first {r['height']}px "
                              f"(overlay only: {r.get('overlayReading')})")


@pytest.mark.parametrize("width", [w for w, _ in VIEWPORTS])
def test_place_reading_above_fold(product, width):
    """Is my air OK, near me? /place shows a reading (number + unit) on the first screen."""
    r = _get(product, "place", width)
    assert r.get("reading"), f"no reading on the first {r['height']}px"


@pytest.mark.parametrize("name,width", CASES, ids=IDS)
def test_map_bounds(product, name, width):
    r = _get(product, name, width)
    if "leaflet.js" in r.get("shimmed", []):
        pytest.skip("Leaflet not vendored locally (no network); see tests/product/README.md")
    for m in r.get("maps", []):
        assert m["width"] > 0 and m["height"] > 0, f"#{m['id']} has no size"
        if m["width"] < 50 or m["height"] < 50:
            continue   # hidden/collapsed map (e.g. a tab not open yet)
        lat, lon = (m["south"] + m["north"]) / 2, (m["west"] + m["east"]) / 2
        assert WV["south"] < lat < WV["north"] and WV["west"] < lon < WV["east"], \
            f"#{m['id']} centered off West Virginia: {lat:.2f},{lon:.2f}"
        assert m["north"] - m["south"] < 12, f"#{m['id']} zoomed out to a continent"
