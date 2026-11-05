# Product harness

Acts as a user: boots the app (uvicorn) on a clone of the local SQLite copy, opens the key
pages in headless Chromium at 390×844 (phone) and 1280×800 (laptop), screenshots them and
checks what a resident would see. Runs as part of `pytest`; `tests/product/run.sh` runs it alone.

Checks (`test_smoke.py`), per page × width unless noted:

- page loads (200, no failed local requests)
- no console errors or uncaught page errors
- nav present; nav count ≤ 7 (xfail until the nav-collapse item)
- home shows a reading (number + unit) on the first screen, not covered and not in an
  overlay (390: xfail until the place view lands)
- every visible Leaflet map has a size and is centered on West Virginia

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
