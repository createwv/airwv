# Product harness

Acts as a user: boots the app (uvicorn) on a clone of the local SQLite copy, opens the key
pages in headless Chromium at 390×844 (phone) and 1280×800 (laptop), screenshots them and
checks what a resident would see. Runs as part of `pytest`; `tests/product/run.sh` runs it alone.

Site pages (`/site/<kind>/<id>`, `SITE_PAGES` in `conftest.py`: one per kind the route serves,
basic and `view=adv`) are shot at every size in `~/.czadmin/tools/devices.json` (phone-small 360,
phone 390, phone-landscape 844, tablet 820, desktop 1280, desktop-wide 1920, with each device's
pixel ratio and touch) and get the same checks, plus no sideways scroll at any size.

The nav's "More" dropdown is tapped open on Home at every devices.json size (`more-<width>.png`,
the menu open in the shot). `test_more_menu_not_cut_off` fails if any of its links, scrolled to,
is still off screen, clipped sideways or under something, or the open menu adds sideways scroll;
the report's "More" table lists the menu box and the links past the first screen (info only).

`/air`'s "Filters" sheet is shot at every devices.json size (`filters-<width>.png`, tapped open
where it's collapsed). `test_air_filters_sheet` wants it collapsed under the map on phones (width
≤ 560 or height ≤ 480), opening with a tap without sideways scroll, and open above the map elsewhere.

Pages with a statewide map (`MAP_PAGES` in `conftest.py`, `map-<page>-<width>.png`) are shot at
every devices.json size for the map check below; the report's `maps` column shows each one's `×` size.

A dialog or map popup, opened as a user would (`OPEN_PAGES` in `conftest.py`: the report modal,
a facility's detail on `/sources`, a sensor's popup on Home's map; `open-<name>-<width>.png`,
open in the shot) is run through the accessibility checks below at every devices.json size.
Those checks look only inside what opened, and the Tab check starts on its first control and
stops when Tab leaves it. `test_dialog_or_popup_opens` fails if it didn't open or Tab can't
get in. Known gaps are in `A11Y_KNOWN` (`test_smoke.py`): xfail, and only for that exact failure.

Checks (`test_smoke.py`), per page × width unless noted:

- page loads (200, no failed local requests)
- no console errors or uncaught page errors
- nav present; nav count ≤ 7 (xfail until the nav-collapse item)
- home shows a reading (number + unit) on the first screen, not covered and not in an
  overlay (390: xfail until the place view lands)
- every visible Leaflet map has a size. Statewide maps' first view contains West Virginia and is
  at most about 2× its size (limit ×2.25, measured along the tighter-fitting side, since a wide
  map always shows more east–west); maps that open on one place (`LOCAL_MAPS`) are only
  checked to be centered on West Virginia
- accessibility (`test_a11y`, per page × width × check; the browser asks for reduced motion):
  text contrast at WCAG AA (4.5:1; 3:1 for large text and for symbols inside a control;
  legend dots outside a control and disabled controls are exempt; text over images/gradients
  can't be measured and is skipped), `<img>` without `alt`, controls without an accessible
  name, mouse-only controls (`cursor:pointer` but not focusable), infinite animations under
  reduced motion, and the first 15 Tab stops each showing a focus ring (a stop without one is
  looked at again 150 ms later). The report's `a11y` column counts what each page fails.
  Checks listed in `A11Y_FLAKY` (now `air`-1280 `noFocus`) are xfail when they fail, with
  what they saw, instead of red.

Output in `tests/product/out/` (gitignored): `<page>-<width>.png` (first screen),
`<page>-<width>-full.png`, `results.json`, `report.md`.

**Data.** `AIRWV_PRODUCT_DB=<path>`, else `airwv.sqlite` in this checkout or the main
checkout (worktrees), else a seeded fake DB (`AIRWV_PRODUCT_DB=seed` forces it). The file is
cloned to a temp dir, so the local copy is never written. `data/*.json` layers are read from
the DB's directory.

**Offline.** Only the local server is reachable. The CDN libraries in `base.html` (Leaflet,
markercluster, Plotly) are served from `$AIRWV_VENDOR_DIR`, `src/airwv/web/static/vendor/` or
`tests/product/vendor/` (gitignored) when a copy is there, else replaced by a no-op shim and
listed in the report; map-bounds checks skip while Leaflet is shimmed. Map tiles are blocked.

**Needs** node and Playwright in `~/.czadmin/tools` (`AIRWV_PLAYWRIGHT_DIR` to override);
the suite skips without them. Inside a sandboxed shell Chromium falls back to
`--single-process`, one browser per page.
