"""Alembic migrations stay in step with the models.

A fresh ``alembic upgrade head`` must build exactly what ``airwv.storage.models``
describes, and a database first built by ``create_schema()`` (then stamped at an
older revision) must still upgrade cleanly.
"""
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from airwv.storage import Store
from airwv.storage.models import Base

INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _cfg(monkeypatch, url: str) -> Config:
    monkeypatch.setenv("AIRWV_DATABASE_URL", url)
    return Config(str(INI))


def _diff(url: str) -> list:
    engine = create_engine(url)
    with engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), Base.metadata)


def test_upgrade_head_matches_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path/'m.sqlite'}"
    command.upgrade(_cfg(monkeypatch, url), "head")
    assert _diff(url) == []


def test_upgrade_over_create_schema_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path/'m.sqlite'}"
    Store(url).create_schema()
    cfg = _cfg(monkeypatch, url)
    command.stamp(cfg, "b3c1f2a9d4e7")   # stamped before the catch-up revision
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "b3c1f2a9d4e7")
    command.upgrade(cfg, "head")
    assert _diff(url) == []
