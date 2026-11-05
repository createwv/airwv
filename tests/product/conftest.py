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
]
VIEWPORTS = [(390, 844), (1280, 800)]   # phone (iPhone 12–15), laptop
GEO = {"lat": 38.35, "lon": -81.63}     # Charleston
VENDOR_DIRS = [os.environ.get("AIRWV_VENDOR_DIR", ""), str(REPO / "src/airwv/web/static/vendor"),
               str(HERE / "vendor")]


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
            "pages": [{"name": n, "path": p} for n, p in PAGES],
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
            "| page | width | status | ms | nav | reading above fold | maps | console/page errors |",
            "|---|---|---|---|---|---|---|---|"]
    for r in data["results"]:
        reading = (r.get("reading") or {}).get("text", "—").replace("|", "/")[:60]
        maps = ", ".join(f"{m['id']} z{m['zoom']}" for m in r.get("maps", [])) or "—"
        errs = len(r["consoleErrors"]) + len(r["pageErrors"])
        rows.append(f"| {r['page']} | {r['width']} | {r['status']} | {r['ms']} | "
                    f"{r.get('navCount', '—')} | {reading} | {maps} | {errs} |")
    shimmed = sorted({s for r in data["results"] for s in r.get("shimmed", [])})
    if shimmed:
        rows += ["", (f"Shimmed (no local copy, no network): {', '.join(shimmed)}. Map/chart "
                      "checks skip until these are vendored (AIRWV_VENDOR_DIR or static/vendor/).")]
    return "\n".join(rows) + "\n"
