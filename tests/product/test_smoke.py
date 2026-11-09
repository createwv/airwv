"""Product smoke suite: the key pages at phone (390) and laptop (1280) widths, as a user sees
them. One harness run per session (conftest.py); screenshots + report.md land in
tests/product/out/. Later plan items cite these checks; known gaps are xfail with the plan
item that should close them, so they turn into XPASS (not red) when it lands.
"""

import pytest
# tests/ isn't a package; pytest puts this dir on sys.path
from conftest import (DEVICES, EXPLORE_SERIES, FILTERS_PAGE, LOCAL_MAPS, MAP_PAGES, MORE_PAGE, PAGES,
                      SITE_PAGES, VIEWPORTS, WV_LINE, map_span)

# West Virginia plus a margin for border sensors (matches the app's region box in spirit).
WV = {"south": 37.0, "north": 40.8, "west": -82.8, "east": -77.5}
MAP_MAX_SPAN = 2.25   # "about 2×": whole zoom levels are 2× apart, plus fitBounds padding
NAV_TARGET = 6  # six top-level items, "More" included (docs/DIRECTION.md §3)

CASES = [(name, w) for w, _ in VIEWPORTS for name, _ in PAGES]
# site pages at every standard screen size (devices.json)
SITE_CASES = [(name, d["viewport"]["width"]) for name, _ in [*SITE_PAGES, EXPLORE_SERIES]
              for d in DEVICES.values()]
CASES += SITE_CASES
IDS = [f"{n}-{w}" for n, w in CASES]
# statewide maps at every standard screen size; these pages only feed test_map_bounds
MAP_CASES = [(name, d["viewport"]["width"]) for name, _ in MAP_PAGES for d in DEVICES.values()]


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


@pytest.mark.parametrize("name,width", SITE_CASES, ids=[f"{n}-{w}" for n, w in SITE_CASES])
def test_site_no_sideways_scroll(product, name, width):
    """Site pages fit every screen size: nothing wider than the viewport (charter rule 6)."""
    r = _get(product, name, width)
    assert r.get("scrollWidth", 0) <= r.get("innerWidth", 0), \
        f"page is {r.get('scrollWidth')}px wide in a {r.get('innerWidth')}px viewport"


@pytest.mark.parametrize("width", [d["viewport"]["width"] for d in DEVICES.values()])
def test_more_menu_not_cut_off(product, width):
    """The "More" dropdown opens and each of its links can be reached at every screen size:
    on screen once scrolled to, not clipped sideways, not under anything (charter rule 6)."""
    r = _get(product, MORE_PAGE[0], width)
    assert r["error"] is None, r["error"]
    m = r.get("more") or {}
    assert "error" not in m, m.get("error")
    assert m.get("open"), "tapping More didn't open the menu"
    assert m.get("links", 0) > 0, "More menu has no links"
    assert m.get("cut") == [], \
        f"More menu cut off in a {m['innerWidth']}x{m['innerHeight']} screen: {m['cut']}"
    assert m["scrollWidth"] <= m["innerWidth"], \
        f"open menu makes the page {m['scrollWidth']}px wide in a {m['innerWidth']}px viewport"


@pytest.mark.parametrize("width", [d["viewport"]["width"] for d in DEVICES.values()])
def test_air_filters_sheet(product, width):
    """Phones get the map first and /air's controls collapsed under it, opening with a tap;
    bigger screens keep them open above the map (same media query as dashboard.html)."""
    r = _get(product, FILTERS_PAGE[0], width)
    assert r["error"] is None, r["error"]
    first = r["filters"]["first"]
    assert "error" not in first, first.get("error")
    if width <= 560 or r["height"] <= 480:
        assert first["belowMap"] and first["summary"], first
        assert not first["open"] and not first["metricVisible"], first
        tapped = r["filters"]["tapped"]
        assert tapped["open"] and tapped["metricVisible"], tapped
        assert tapped["scrollWidth"] <= tapped["innerWidth"], \
            f"open sheet makes the page {tapped['scrollWidth']}px wide in {tapped['innerWidth']}px"
    else:
        assert not first["belowMap"] and not first["summary"], first
        assert first["open"] and first["metricVisible"], first


@pytest.mark.parametrize("width", [d["viewport"]["width"] for d in DEVICES.values()])
def test_explore_measurements(product, width):
    """Explore's measurements option: a site is picked and its readings listed with links to
    the same series, the form switches back to places and over to another place without
    carrying the site along, and the page's URL reproduces the table."""
    r = _get(product, EXPLORE_SERIES[0], width)
    assert r["error"] is None, r["error"]
    s = r["series"]
    first, listed, back, moved, link = s["first"], s["list"], s["back"], s["moved"], s["link"]
    assert first["site"] and first["urlSite"] == first["site"] and first["rows"] > 0, first
    assert first["siteShown"] and first["fromShown"] and first["linksShown"], first
    assert not first["sinceShown"] and not first["kindsShown"], first
    assert "site_id=" in first["api"] and "param=pm2_5" in first["api"] and "show=" not in first["api"], first
    assert first["csv"] == first["api"] + "&format=csv", first
    assert not listed["siteShown"] and listed["sinceShown"] and listed["kindsShown"], listed
    assert listed["urlSite"] is None and listed["urlShow"] == "sites" and "site_id=" not in listed["api"], listed
    assert back["site"] == first["site"] and back["rows"] > 0, back
    assert moved["site"] != first["site"], moved
    assert link["site"] == first["site"] and link["rows"] == first["rows"], link


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


def _first_view_problem(m):
    """Why a statewide map's first view is wrong: must hold the state line, at most ~2× its size."""
    if not (m["south"] <= WV_LINE["south"] and m["north"] >= WV_LINE["north"]
            and m["west"] <= WV_LINE["west"] and m["east"] >= WV_LINE["east"]):
        return (f"cuts off West Virginia: view {m['south']:.2f},{m['west']:.2f} to "
                f"{m['north']:.2f},{m['east']:.2f}")
    if map_span(m) > MAP_MAX_SPAN:
        return f"opens at {map_span(m):.2f}× West Virginia's size (limit {MAP_MAX_SPAN})"
    return None


@pytest.mark.parametrize("view,problem", [
    ({"south": 35.44, "north": 42.28, "west": -84.00, "east": -76.35}, None),        # phone, z6
    ({"south": 35.44, "north": 42.28, "west": -90.70, "east": -69.65}, None),        # wide map, z6
    ({"south": 37.30, "north": 40.60, "west": -82.60, "east": -77.80}, "cuts off"),
    ({"south": 32.00, "north": 45.00, "west": -96.00, "east": -64.00}, "opens at"),  # z5
])
def test_first_view_rule(view, problem):
    found = _first_view_problem(view)
    assert (found is None) if problem is None else (problem in found), found


@pytest.mark.parametrize("name,width", CASES + MAP_CASES,
                         ids=IDS + [f"{n}-{w}" for n, w in MAP_CASES])
def test_map_bounds(product, name, width):
    r = _get(product, name, width)
    if "leaflet.js" in r.get("shimmed", []):
        pytest.skip("Leaflet not vendored locally (no network); see tests/product/README.md")
    sized = 0
    for m in r.get("maps", []):
        assert m["width"] > 0 and m["height"] > 0, f"#{m['id']} has no size"
        if m["width"] < 50 or m["height"] < 50:
            continue   # hidden/collapsed map (e.g. a tab not open yet)
        sized += 1
        if m["id"] not in LOCAL_MAPS:
            problem = _first_view_problem(m)
            assert problem is None, f"#{m['id']} {problem}"
            continue
        lat, lon = (m["south"] + m["north"]) / 2, (m["west"] + m["east"]) / 2
        assert WV["south"] < lat < WV["north"] and WV["west"] < lon < WV["east"], \
            f"#{m['id']} centered off West Virginia: {lat:.2f},{lon:.2f}"
        assert m["north"] - m["south"] < 12, f"#{m['id']} zoomed out to a continent"
    if (name, width) in MAP_CASES:
        assert sized, f"no map to check ({r['error'] or 'page loaded'})"


# Accessibility (harness.mjs a11yProbe/focusProbe; the browser asks for reduced motion).
A11Y = {
    "contrast": "text below WCAG AA contrast (4.5:1; 3:1 for large text or symbols in a control)",
    "noAlt": "images with no alt attribute",
    "noName": "controls with no accessible name",
    "clickable": "mouse-only controls (cursor:pointer, not keyboard-focusable)",
    "motion": "infinite animations despite prefers-reduced-motion",
    "noFocus": "tab stops with no visible focus indicator",
}


@pytest.mark.parametrize("check", list(A11Y))
@pytest.mark.parametrize("name,width", CASES, ids=IDS)
def test_a11y(product, name, width, check):
    a = _get(product, name, width).get("a11y") or {}
    assert "error" not in a, a.get("error")
    assert a.get(check) == [], f"{A11Y[check]}: {a.get(check)}"
