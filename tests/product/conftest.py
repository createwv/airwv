"""Product harness fixtures: boot the real app on a local SQLite copy, drive it with headless
Chromium (tests/product/harness.mjs, Playwright from ~/.czadmin/tools), hand results to tests.

Which database:
  AIRWV_PRODUCT_DB=<path>   use that SQLite file (`seed` = always the seeded fake DB)
  otherwise                the checkout's own airwv.sqlite (worktrees fall back to the main
                            checkout's), else a small seeded fake DB so the suite still runs.
The file is cloned to a temp dir first, so the harness never writes to the local copy.
Data-layer JSON (data/*.json) is read from the directory that holds the DB.

Skips (not fails) when node or Playwright isn't installed. No network: only the local server
is reachable; see harness.mjs for how CDN libraries are vendored or shimmed.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).parent
REPO = HERE.parent.parent
OUT = HERE / "out"
PW_DIR = Path(os.environ.get("AIRWV_PLAYWRIGHT_DIR", Path.home() / ".czadmin/tools"))

# The key pages, in nav order. Nearby deep-links to Charleston so it loads without a click.
PAGES = [
    ("home", "/"),
    ("place", "/place?place=rutledge&km=8"),   # "my place now" at the demo place, 5 mi
    ("air", "/air"),
    ("nearby", "/nearby?lat=38.35&lon=-81.63"),
    ("water", "/water"),
    ("sources", "/sources"),
    ("events", "/events"),
    ("spills", "/spills"),
    ("learn", "/learn"),
    ("alerts", "/alerts"),
    ("about", "/about"),
    ("data", "/data"),
    ("explore", "/explore?place=rutledge&km=8&param=pm2_5"),   # query builder at the demo place
    # data centers: Near me at Point Pleasant (Monarch is ~4 mi); its site page is in SITE_PAGES
    ("nearby-dc", "/nearby?lat=38.86&lon=-82.13&from=Point+Pleasant"),
    ("place-dc", "/place?lat=38.86&lon=-82.13&name=Point+Pleasant&km=8"),
    ("place-dc-adv", "/place?lat=38.86&lon=-82.13&name=Point+Pleasant&km=8&view=adv"),
]
VIEWPORTS = [(390, 844), (1280, 800)]   # phone (iPhone 12–15), laptop
# `/site/<kind>/<id>`: one page per kind the route serves, shot at every DEVICES size. Add a
# line here as each kind lands (sensor, monitor, gauge, facility, water system, well, spill).
SITE_PAGES = [
    ("site-dc", "/site/data_center/monarch-compute-campus"),
    ("site-dc-adv", "/site/data_center/monarch-compute-campus?view=adv"),
]
# Pages whose maps open on the whole state, shot at every DEVICES size for the map check.
MAP_PAGES = [("map-home", "/"), ("map-air", "/air"), ("map-water", "/water"),
             ("map-spills", "/spills")]
# Maps that open on one place by design, so they aren't held to the statewide first view.
LOCAL_MAPS = {"nb-map"}
# The state line (WV_BBOX in static/wv.js).
WV_LINE = {"south": 37.20, "north": 40.64, "west": -82.65, "east": -77.72}
# The nav's "More" dropdown, opened on Home and shot at every DEVICES size (`more-<width>.png`).
MORE_PAGE = ("more", "/")
# /air's "Filters" sheet, tapped open where it's collapsed and shot at every DEVICES size.
FILTERS_PAGE = ("filters", "/air")
# A dialog or map popup opened as a user would, shot at every DEVICES size; the a11y checks run
# on what opens (`scope`), since the pages under them are already checked in PAGES.
OPEN_PAGES = [
    ("open-report", "/air", {"click": '[data-open="report"]', "scope": "#reportmodal.on"}),
    ("open-facility", "/sources", {"click": ".srccard", "scope": "#srcdetail.on"}),
    # Home's map, not /air's: clicking an /air sensor redraws the markers, closing its popup at once
    ("open-sensor", "/", {"marker": {"map": "ov-map", "text": "µg/m³ PM2.5"},
                          "scope": "#ov-map .leaflet-popup"}),
]
# Explore's measurements option, driven through its form at every DEVICES size.
EXPLORE_SERIES = ("explore-series", "/explore?show=measurements&place=rutledge&km=8&param=pm2_5")
# Explore with its county and value filters set from the URL, at every DEVICES size.
EXPLORE_FILTERS = ("explore-filters", "/explore?place=kanawha&km=40.25&county=Kanawha&param=pm2_5&over=1")
# The standard screen sizes (charter rule 6): ~/.czadmin/tools/devices.json, else this copy.
DEVICES = {
    "phone-small": {"viewport": {"width": 360, "height": 780}, "deviceScaleFactor": 3,
                    "isMobile": True, "hasTouch": True},
    "phone": {"viewport": {"width": 390, "height": 844}, "deviceScaleFactor": 3,
              "isMobile": True, "hasTouch": True},
    "phone-landscape": {"viewport": {"width": 844, "height": 390}, "deviceScaleFactor": 3,
                        "isMobile": True, "hasTouch": True},
    "tablet": {"viewport": {"width": 820, "height": 1180}, "deviceScaleFactor": 2,
               "isMobile": True, "hasTouch": True},
    "desktop": {"viewport": {"width": 1280, "height": 800}, "deviceScaleFactor": 1,
                "isMobile": False, "hasTouch": False},
    "desktop-wide": {"viewport": {"width": 1920, "height": 1080}, "deviceScaleFactor": 1,
                     "isMobile": False, "hasTouch": False},
}
try:
    DEVICES = {k: v for k, v in json.loads((PW_DIR / "devices.json").read_text()).items()
               if isinstance(v, dict) and "viewport" in v} or DEVICES
except (OSError, ValueError):
    pass
GEO = {"lat": 38.35, "lon": -81.63}     # Charleston
VENDOR_DIRS = [os.environ.get("AIRWV_VENDOR_DIR", ""), str(REPO / "src/airwv/web/static/vendor"),
               str(HERE / "vendor")]


def _device_viewports() -> list[dict]:
    return [{"w": d["viewport"]["width"], "h": d["viewport"]["height"], "device": name,
             "scale": d.get("deviceScaleFactor", 1), "mobile": bool(d.get("isMobile")),
             "touch": bool(d.get("hasTouch"))} for name, d in DEVICES.items()]


def map_span(m: dict) -> float:
    """How many times West Virginia's size a map's view is, along its tighter-fitting side.
    The other side is set by the map's shape: a 958x400 map shows ~4x the state's width at best."""
    return min((m["north"] - m["south"]) / (WV_LINE["north"] - WV_LINE["south"]),
               (m["east"] - m["west"]) / (WV_LINE["east"] - WV_LINE["west"]))


def _main_checkout() -> Path | None:
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=REPO, text=True,
                                capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return (REPO / common).resolve().parent


def _find_db() -> Path | None:
    env = os.environ.get("AIRWV_PRODUCT_DB", "").strip()
    if env == "seed":
        return None
    if env:
        return Path(env)
    for root in (REPO, _main_checkout()):
        if root and _has_readings(root / "airwv.sqlite"):
            return root / "airwv.sqlite"
    return None


def _has_readings(path: Path) -> bool:
    # importing airwv.web.app creates an empty airwv.sqlite in cwd; don't mistake it for data
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            return con.execute("SELECT 1 FROM readings LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def _clone(src: Path, dst: Path) -> None:
    # APFS copy-on-write clone is instant even for the ~650 MB local copy; plain copy otherwise
    if sys.platform == "darwin" and subprocess.run(["cp", "-c", str(src), str(dst)],
                                                   check=False, capture_output=True).returncode == 0:
        return
    shutil.copyfile(src, dst)


def _seed_db(path: Path) -> None:
    """Fake readings near Charleston/Morgantown, for when no local copy exists (e.g. CI)."""
    sys.path.insert(0, str(REPO / "src"))
    from airwv.sources.base import Reading
    from airwv.storage import Store

    store = Store(f"sqlite:///{path}")
    store.create_schema()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    sites = [("fake-1", 38.35, -81.63), ("fake-2", 38.42, -81.80), ("fake-3", 39.63, -79.96)]
    store.save_readings([
        Reading(source="purpleair", sensor_id=sid, ts=now - timedelta(hours=h), lat=lat, lon=lon,
                pm2_5=6.0 + i * 3 + (h % 4))
        for i, (sid, lat, lon) in enumerate(sites) for h in range(48)
    ])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def product(tmp_path_factory):
    """Run the harness once per session; returns {"db": ..., "results": [...]}."""
    node = shutil.which("node")
    if not node or not (PW_DIR / "node_modules/playwright").is_dir():
        pytest.skip(f"product harness needs node + Playwright in {PW_DIR}")

    tmp = tmp_path_factory.mktemp("product")
    db = tmp / "airwv.sqlite"
    src = _find_db()
    if src:
        _clone(src, db)
        data_root = src.parent
    else:
        _seed_db(db)
        data_root = tmp

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "AIRWV_DATABASE_URL": f"sqlite:///{db}",
           "PYTHONPATH": os.pathsep.join([str(REPO / "src"), os.environ.get("PYTHONPATH", "")]),
           "AIRWV_FIELD_PHOTOS": str(tmp / "field_photos"),
           "AIRWV_REPORT_PHOTOS": str(tmp / "report_photos"),
           "AIRWV_ADMIN_TOKEN": "", "AIRWV_GOOGLE_MAPS_KEY": ""}
    log = open(tmp / "server.log", "w")  # noqa: SIM115 - closed in the finally below
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "airwv.web.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=data_root, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 60
        while True:
            try:
                if httpx.get(base + "/about", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if server.poll() is not None or time.time() > deadline:
                pytest.fail("app did not start:\n" + (tmp / "server.log").read_text()[-2000:])
            time.sleep(0.3)

        cfg = tmp / "harness.json"
        cfg.write_text(json.dumps({
            "base": base, "out": str(OUT), "geo": GEO,
            "pages": [{"name": n, "path": p} for n, p in PAGES]
            + [{"name": n, "path": p, "viewports": _device_viewports()} for n, p in SITE_PAGES]
            + [{"name": n, "path": p, "viewports": _device_viewports()} for n, p in MAP_PAGES]
            + [{"name": MORE_PAGE[0], "path": MORE_PAGE[1], "viewports": _device_viewports(),
                "openMore": True}]
            + [{"name": FILTERS_PAGE[0], "path": FILTERS_PAGE[1], "viewports": _device_viewports(),
                "openFilters": True}]
            + [{"name": EXPLORE_SERIES[0], "path": EXPLORE_SERIES[1], "viewports": _device_viewports(),
                "driveSeries": True}]
            + [{"name": EXPLORE_FILTERS[0], "path": EXPLORE_FILTERS[1], "viewports": _device_viewports(),
                "probeExFilters": True}]
            + [{"name": n, "path": p, "viewports": _device_viewports(), "open": o}
               for n, p, o in OPEN_PAGES],
            "viewports": [{"w": w, "h": h} for w, h in VIEWPORTS],
            "vendorDirs": [d for d in VENDOR_DIRS if d],
        }))
        run = subprocess.run([node, str(HERE / "harness.mjs"), str(cfg)], text=True,
                             check=False, capture_output=True, timeout=900)
        if run.returncode != 0:
            err = "\n".join(ln for ln in run.stderr.splitlines() if "[pid=" not in ln)
            pytest.fail(f"harness.mjs failed:\n{err[-3000:]}")
        results = json.loads(run.stdout)
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()

    data = {"db": str(src) if src else "seeded fake DB", "base": base, "results": results}
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(data, indent=2))
    (OUT / "report.md").write_text(_report(data))
    return data


def _report(data) -> str:
    rows = ["# Product harness report", "",
            f"DB: `{data['db']}` · {datetime.now().isoformat(timespec='minutes')}", "",
            "| page | width | status | ms | nav | reading above fold | maps | console/page errors "
            "| a11y |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in data["results"]:
        reading = (r.get("reading") or {}).get("text", "—").replace("|", "/")[:60]
        maps = ", ".join(f"{m['id']} z{m['zoom']}"
                         + ("" if m["id"] in LOCAL_MAPS else f" ×{map_span(m):.2f}")
                         for m in r.get("maps", [])) or "—"
        errs = len(r["consoleErrors"]) + len(r["pageErrors"])
        a11y = ", ".join(f"{k} {len(v)}" for k, v in (r.get("a11y") or {}).items()
                         if isinstance(v, list) and v) or "ok"
        rows.append(f"| {r['page']} | {r['width']} | {r['status']} | {r['ms']} | "
                    f"{r.get('navCount', '—')} | {reading} | {maps} | {errs} | {a11y} |")
    more = [r for r in data["results"] if "more" in r]
    if more:
        rows += ["", '## "More" menu, open', "",
                 "| device | width | menu box (top,left–right,bottom) | links | cut off "
                 "| past first screen |", "|---|---|---|---|---|---|"]
        for r in more:
            m = r["more"]
            if "error" in m:
                rows.append(f"| {r.get('device')} | {r['width']} | {m['error']} | | | |")
                continue
            b = m["rect"]
            rows.append(f"| {r.get('device')} | {r['width']} | {b['top']},{b['left']}–"
                        f"{b['right']},{b['bottom']} | {m['links']} | "
                        f"{', '.join(m['cut']) or 'none'} | {', '.join(m['belowFold']) or '—'} |")
    shimmed = sorted({s for r in data["results"] for s in r.get("shimmed", [])})
    if shimmed:
        rows += ["", (f"Shimmed (no local copy, no network): {', '.join(shimmed)}. Map/chart "
                      "checks skip until these are vendored (AIRWV_VENDOR_DIR or static/vendor/).")]
    return "\n".join(rows) + "\n"
