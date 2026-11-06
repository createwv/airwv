"""Nightly public dumps: the three read views as gzipped CSV, plus a manifest.

``python -m airwv.dumps [--out DIR]`` writes, into ``AIRWV_DUMPS_DIR`` (default
``data/dumps``, gitignored):

* ``site.csv.gz``        — every located thing (``attrs`` is the layer's JSON record)
* ``measurement.csv.gz`` — every air and water value, tall (``raw`` provider payloads left out)
* ``record.csv.gz``      — violations, statuses, listings, spills
* ``manifest.json``      — when, row counts, columns, and each layer file's provenance block

Only the public views are read (storage/readmodel.py), never ``reports`` / ``feedback`` /
``subscriptions``, so no contact details, IP hashes or exact report coordinates. Each file
is written beside its final name and renamed into place, so the web app never serves a
half-written dump. The app serves them at ``/data/dumps/<name>`` (web/app.py).

CSV only: Parquet needs ``pyarrow``, which isn't a dependency (yet). SQLite for now; the
views move to Postgres with the migration, and this reads them the same way.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from airwv.storage import Store
from airwv.storage.readmodel import FILE_LAYERS

log = logging.getLogger(__name__)

# dump name -> (view, columns, ORDER BY); the order keeps nightly files diff-friendly
TABLES = {
    "site": ("site", ["site_id", "kind", "medium", "layer", "ref", "name", "lat", "lon",
                      "county", "url", "attrs"], "layer, ord, ref"),
    "measurement": ("measurement", ["medium", "source", "ref", "ts", "parameter", "value",
                                    "unit", "quality"], "medium, ref, parameter, ts"),
    "record": ("record", ["site_id", "layer", "kind", "value", "effective", "url", "as_of"],
               "layer, site_id, kind"),
}

FILES = [f"{name}.csv.gz" for name in TABLES] + ["manifest.json"]

CAVEATS = ("Public data, re-published as received: government data is provisional and not a "
           "regulatory determination; community-sensor (PurpleAir) values are as the sensor reported "
           "them, without the EPA PM2.5 correction. "
           "Each layer's source, scope and disclaimer are under 'provenance'; see "
           "https://air.createwv.org/data.")


def default_dir() -> Path:
    return Path(os.environ.get("AIRWV_DUMPS_DIR", "").strip() or "data/dumps")


def _write_table(conn, view: str, columns: list[str], order: str, path: Path) -> int:
    tmp = path.with_name(path.name + ".tmp")
    n = 0
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        result = conn.exec_driver_sql(f"SELECT {', '.join(columns)} FROM {view} ORDER BY {order}")
        while rows := result.fetchmany(5000):
            writer.writerows(rows)
            n += len(rows)
    tmp.replace(path)
    return n


def write_dumps(store: Store, out_dir: Path | str | None = None, now: datetime | None = None) -> dict:
    """Write every dump into ``out_dir``; returns the manifest (also written as manifest.json)."""
    out = Path(out_dir) if out_dir is not None else default_dir()
    out.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    manifest = {"generated_at": now.isoformat(timespec="seconds"), "caveats": CAVEATS, "files": {}}
    with store.read_model.connect() as conn:
        for name, (view, columns, order) in TABLES.items():
            fname = f"{name}.csv.gz"
            rows = _write_table(conn, view, columns, order, out / fname)
            manifest["files"][fname] = {"view": view, "rows": rows, "columns": columns,
                                        "bytes": (out / fname).stat().st_size}
            log.info("dumped %s: %d rows -> %s", view, rows, out / fname)
        manifest["provenance"] = store.read_model.provenance(conn, list(FILE_LAYERS))
    buf = io.StringIO()
    json.dump(manifest, buf, indent=2, ensure_ascii=False)
    tmp = out / "manifest.json.tmp"
    tmp.write_text(buf.getvalue() + "\n", encoding="utf-8")
    tmp.replace(out / "manifest.json")
    return manifest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="airwv.dumps", description="Write the nightly public dumps")
    parser.add_argument("--out", help="output directory (default $AIRWV_DUMPS_DIR or data/dumps)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = os.environ.get("AIRWV_DATABASE_URL", "").strip() or "sqlite:///airwv.sqlite"
    write_dumps(Store(url), args.out)


if __name__ == "__main__":
    try:  # a local .env (AIRWV_DATABASE_URL / AIRWV_DUMPS_DIR); optional in prod
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover
        pass
    main()
