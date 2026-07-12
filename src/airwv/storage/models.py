"""Database models for AirWV storage.

One table, ``readings``, holds normalized air-quality readings. It mirrors the
:class:`~airwv.sources.base.Reading` schema plus bookkeeping columns:

- ``quality`` — set by the analysis stage (``ok`` / ``suspect`` / ``malfunction``).
  Defaults to ``ok`` at ingest; we flag, never delete (see ARCHITECTURE.md).
- ``raw`` — the original provider payload, so readings can be reprocessed later.
- ``ingested_at`` — when AirWV stored the row.

A uniqueness constraint on ``(source, sensor_id, ts)`` gives us idempotent writes:
re-ingesting the same reading is a no-op rather than a duplicate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware current UTC time (used as the ``ingested_at`` default)."""
    return datetime.now(tz=timezone.utc)


class Base(DeclarativeBase):
    pass


class ReadingRow(Base):
    """A stored, normalized air-quality reading."""

    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("source", "sensor_id", "ts", name="uq_reading_source_sensor_ts"),
        # per-sensor time-ordered lookups (latest value, coverage, series) at scale
        Index("ix_reading_sensor_ts", "sensor_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(64), index=True)
    sensor_id: Mapped[str] = mapped_column(String(128), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    pm1_0: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm2_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm10: Mapped[float | None] = mapped_column(Float, nullable=True)
    aqi: Mapped[float | None] = mapped_column(Float, nullable=True)
    voc: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    pressure: Mapped[float | None] = mapped_column(Float, nullable=True)

    pm2_5_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm2_5_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    count_0_3: Mapped[float | None] = mapped_column(Float, nullable=True)
    count_0_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    count_1_0: Mapped[float | None] = mapped_column(Float, nullable=True)
    count_2_5: Mapped[float | None] = mapped_column(Float, nullable=True)
    count_5_0: Mapped[float | None] = mapped_column(Float, nullable=True)
    count_10_0: Mapped[float | None] = mapped_column(Float, nullable=True)

    quality: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Subscription(Base):
    """A person/system asking to be alerted when a field crosses a threshold."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    channel: Mapped[str] = mapped_column(String(16))  # email | sms | webhook | log
    target: Mapped[str] = mapped_column(String(256))  # address / phone / URL

    # kind "threshold": fire when latest field >= threshold.
    # kind "trend":     fire when field is rising by >= threshold percent.
    kind: Mapped[str] = mapped_column(String(16), default="threshold")

    # None sensor_id = any sensor. field/threshold define the trigger.
    sensor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    field: Mapped[str] = mapped_column(String(32), default="pm2_5")
    threshold: Mapped[float] = mapped_column(Float)

    active: Mapped[bool] = mapped_column(default=True)
    # Rate limiting + quiet hours (local) so people aren't spammed.
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=21600)  # 6h
    quiet_start: Mapped[int | None] = mapped_column(Integer, nullable=True)  # local hour
    quiet_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Report(Base):
    """A community-reported environmental concern. Staged trust model (see
    docs/COMMUNITY-REPORTING.md): a light report is auto-screened, published as
    *unverified* (or *held*), then a maintainer can verify/enrich it. Sensitive
    fields (suspected org, contact, ip) stay private unless a reviewer approves them.
    """

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    domain: Mapped[str] = mapped_column(String(32), index=True)   # air|water|soil|wildlife|violation|other
    category: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(String(2000), default="")

    lat: Mapped[float] = mapped_column(Float)                     # exact (stored); public view jittered
    lon: Mapped[float] = mapped_column(Float)
    area_label: Mapped[str | None] = mapped_column(String(120), nullable=True)  # coarse area, not a street

    # published_unverified | held | confirmed | removed | merged
    stage: Mapped[str] = mapped_column(String(24), default="published_unverified", index=True)
    screen_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)  # why auto-held
    flags_count: Mapped[int] = mapped_column(Integer, default=0)
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # naming is input-allowed but publish-gated
    suspected_org: Mapped[str | None] = mapped_column(String(200), nullable=True)  # PRIVATE until org_public
    org_public: Mapped[bool] = mapped_column(default=False)

    photo_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    photo_ok: Mapped[bool] = mapped_column(default=False)  # held until a maintainer approves the image

    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)  # PRIVATE — never public
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)   # PRIVATE
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)          # PRIVATE — rate-limit/abuse
    mod_note: Mapped[str | None] = mapped_column(String(500), nullable=True)        # PRIVATE — maintainer notes


class CommunityReading(Base):
    """An optional structured measurement a resident submits (air/water/soil), tied
    to a report or standalone. Clearly community-submitted; not mixed into the sensor
    network unless verified."""

    __tablename__ = "readings_community"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    domain: Mapped[str] = mapped_column(String(32))
    parameter: Mapped[str] = mapped_column(String(64))   # PM2.5, pH, turbidity, ...
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    method: Mapped[str | None] = mapped_column(String(200), nullable=True)  # device / how measured
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Feedback(Base):
    """Site feedback — a bug report / idea / question about the website itself.
    Routed to maintainers; not shown on the map."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="idea")  # bug | idea | question
    message: Mapped[str] = mapped_column(String(2000))
    page: Mapped[str | None] = mapped_column(String(300), nullable=True)   # url/context
    contact: Mapped[str | None] = mapped_column(String(200), nullable=True)  # PRIVATE
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new | triaged | done
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)   # PRIVATE


class Event(Base):
    """A curated air/pollution event, marked to a region + time window. Two classes:
    'captured' (our sensors measured it — has sensor_ids to overlay) and documented/
    historical (context only, no sensor data). Admin-curated."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default="other")   # fire | explosion | haze | spill | odor | other
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)  # place / WV region
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(300), nullable=True)   # likely/suspected/known cause
    scope: Mapped[str | None] = mapped_column(String(60), nullable=True)     # Local | Regional | Multi-state | Continental
    regions_affected: Mapped[str | None] = mapped_column(String(500), nullable=True)  # free-text extent

    captured: Mapped[bool] = mapped_column(default=False)   # do our sensors have data for it?
    sensor_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)   # ["214373", ...]
    source_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # facility names (link to /sources)
    report_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)   # community report ids
    sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)      # [{"label","url"}, ...] citations
    status: Mapped[str] = mapped_column(String(16), default="published", index=True)  # published | draft | archived
