"""Nightly public dumps: the three read views and published events as gzipped CSV, plus a manifest.

``python -m airwv.dumps [--out DIR]`` writes, into ``AIRWV_DUMPS_DIR`` (default
``data/dumps``, gitignored):

* ``site.csv.gz``        — every located thing (``attrs`` is the layer's JSON record)
* ``measurement.csv.gz`` — every air and water value, tall (``raw`` provider payloads left out)
* ``record.csv.gz``      — violations, statuses, listings, spills
* ``events.csv.gz``      — curated events, published only (no drafts or archived ones)
* ``manifest.json``      — when, row counts, columns, and each layer file's provenance block

plus the same four tables as ``<name>.parquet`` (typed columns, zstd) when ``pyarrow`` is
installed (the ``dumps`` extra); without it the CSVs are still written and Parquet is skipped
with a warning.

Only the public views (storage/readmodel.py) and published ``events`` are read, never
``reports`` / ``feedback`` / ``subscriptions``, so no contact details, IP hashes or exact
report coordinates. Each file
is written beside its final name and renamed into place, so the web app never serves a
half-written dump. The app serves them at ``/data/dumps/<name>`` (web/app.py).

SQLite for now; the
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
from zoneinfo import ZoneInfo

from airwv.storage import Store
from airwv.storage.readmodel import FILE_LAYERS

log = logging.getLogger(__name__)

# dump name -> (view or filtered table, columns, ORDER BY); the order keeps nightly files diff-friendly
TABLES = {
    "site": ("site", ["site_id", "kind", "medium", "layer", "ref", "name", "lat", "lon",
                      "county", "url", "attrs"], "layer, ord, ref"),
    "measurement": ("measurement", ["medium", "source", "ref", "ts", "parameter", "value",
                                    "unit", "quality"], "medium, ref, parameter, ts"),
    "record": ("record", ["site_id", "layer", "kind", "value", "effective", "url", "as_of"],
               "layer, site_id, kind"),
    "events": ("events WHERE status = 'published'",
               ["id", "title", "medium", "kind", "region", "lat", "lon", "start_ts", "end_ts",
                "description", "origin", "scope", "regions_affected", "captured", "sensor_ids",
                "source_refs", "report_ids", "sources"], "start_ts, id"),
}

# Parquet column types; every other column is a string, as in the CSV. Fixed per table because
# SQLite's types are loose, so a type guessed from the first batch could break a later one.
PARQUET_TYPES = {
    "site": {"lat": "float64", "lon": "float64"},
    "measurement": {"value": "float64"},
    "record": {},
    "events": {"id": "int64", "lat": "float64", "lon": "float64", "captured": "bool"},
}
_CASTS = {"string": str, "float64": float, "int64": int, "bool": bool}

FILES = ([f"{name}.csv.gz" for name in TABLES] + [f"{name}.parquet" for name in TABLES]
         + ["manifest.json"])

CAVEATS = ("Public data, re-published as received: government data is provisional and not a "
           "regulatory determination; community-sensor (PurpleAir) values are as the sensor reported "
           "them, without the EPA PM2.5 correction. "
           "Events are curated by AirWV; each row's 'sources' lists its citations. "
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


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        log.warning("pyarrow not installed (pip install '.[dumps]'): skipping the Parquet dumps")
        return None
    return pa, pq


def _write_parquet(conn, view: str, columns: list[str], order: str, types: dict, path: Path,
                   pa, pq) -> int:
    schema = pa.schema([(c, pa.type_for_alias(types.get(c, "string"))) for c in columns])
    casts = [_CASTS[types.get(c, "string")] for c in columns]   # SQLite hands back 0/1, "2.4", ...
    tmp = path.with_name(path.name + ".tmp")
    n = 0
    with pq.ParquetWriter(tmp, schema, compression="zstd") as writer:
        result = conn.exec_driver_sql(f"SELECT {', '.join(columns)} FROM {view} ORDER BY {order}")
        while rows := result.fetchmany(5000):
            arrays = [pa.array([None if v is None else cast(v) for v in vals], type=schema.field(c).type)
                      for c, cast, vals in zip(columns, casts, zip(*rows))]
            writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
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
        if arrow := _pyarrow():
            for name, (view, columns, order) in TABLES.items():
                fname = f"{name}.parquet"
                rows = _write_parquet(conn, view, columns, order, PARQUET_TYPES[name],
                                      out / fname, *arrow)
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


def human_size(n: int) -> str:
    if n < 1000:
        return f"{n} bytes"
    if n < 999_500:
        return f"{n / 1e3:.0f} KB"
    if n < 999_950_000:
        return f"{n / 1e6:.1f} MB"
    return f"{n / 1e9:.1f} GB"


def summary(out_dir: Path | str | None = None) -> dict | None:
    """What /data shows from manifest.json: ``as_of`` (Eastern) and ``sizes`` by file name.
    None until the first run has written it, or if it can't be read, so the page still renders."""
    path = (Path(out_dir) if out_dir is not None else default_dir()) / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        made = datetime.fromisoformat(manifest["generated_at"]).astimezone(ZoneInfo("America/New_York"))
        sizes = {name: human_size(info["bytes"]) for name, info in manifest["files"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if not isinstance(exc, FileNotFoundError):
            log.warning("unreadable dump manifest %s: %s", path, exc)
        return None
    return {"as_of": made.strftime("%b %-d, %Y, %-I:%M %p ET"), "sizes": sizes}


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
