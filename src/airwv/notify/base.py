"""Notifier interface + a factory that picks a channel by name.

Each channel turns an :class:`~airwv.alerts.Alert` into a delivered message.
The ``log`` channel always works (useful for dev and dry runs); ``email`` and
``webhook`` need configuration (SMTP creds / a URL) supplied via the environment
or the subscription's target.
"""

from __future__ import annotations

import abc
import logging

log = logging.getLogger("airwv.notify")


RECORD_LABELS = {"spills": ("reported spill", "reported spills"),
                 "water": ("drinking-water violation", "drinking-water violations"),
                 "violations": ("facility violation", "facility violations")}

MAX_RECORD_LINES = 10


def record_line(rec: dict) -> str:
    """One plain, sourced line per record — what the public record says, no more."""
    a = rec.get("attrs") or {}
    county = f"{rec['county']} County" if rec.get("county") else "WV"
    where = f"{county} (location approximate: county only)" if rec.get("county_placed") else county
    if rec["kind"] == "spill":
        what = ", ".join(m["name"] for m in a.get("materials", [])[:2] if m.get("name")) or "a release"
        water = f"; reported reaching {a.get('body_of_water') or 'water'}" if a.get("reached_water") else ""
        return (f"{rec.get('effective') or 'Undated'}: a spill of {what} was reported to the National "
                f"Response Center, {where}{water}.")
    if rec["kind"] == "health_violation":
        return (f"{rec['name']} ({where}, serves {a.get('population', 'an unknown number of')} people): "
                f"EPA lists a health-based drinking-water violation"
                + (f" ({rec['value']})" if rec.get("value") else "") + ".")
    if rec["kind"] == "violation":
        status = (rec.get("value") or "violation").replace("_", " ")
        return f"{rec['name']} ({where}): EPA ECHO lists the facility in {status}."
    return f"{rec.get('name') or rec['site_id']} ({where}): {rec['kind']} {rec.get('value') or ''}".strip()


def alert_subject(alert) -> str:
    if getattr(alert, "kind", "threshold") == "record":
        n = len(alert.records)
        noun = RECORD_LABELS.get(alert.field, (alert.field, alert.field))[n != 1]
        return f"AirWV: {n} new {noun} near {alert.label or 'your place'}"
    if getattr(alert, "kind", "threshold") == "trend":
        return f"AirWV trend alert: {alert.field} rising +{alert.value}% at sensor {alert.sensor_id}"
    return f"AirWV alert: {alert.field} {alert.value} ≥ {alert.threshold} at sensor {alert.sensor_id}"


def alert_body(alert) -> str:
    footer = "\n\n— AirWV, West Virginia community air monitoring"
    if getattr(alert, "unsubscribe_url", None):
        footer += f"\nUnsubscribe: {alert.unsubscribe_url}"
    if getattr(alert, "kind", "threshold") == "record":
        recs = alert.records
        lines = []
        for r in recs[:MAX_RECORD_LINES]:
            lines.append(f"• {record_line(r)}")
            if r.get("url"):
                lines.append(f"  {r['url']}")
        if len(recs) > MAX_RECORD_LINES:
            lines.append(f"…and {len(recs) - MAX_RECORD_LINES} more.")
        sources = sorted({r["source"] for r in recs if r.get("source")})
        return (f"New in the public records AirWV follows, near {alert.label or 'your place'}:\n\n"
                + "\n".join(lines)
                + "\n\nThese are public records, not findings of harm; the date is the record's own, "
                "and it can be older than the day it reached us. Check the linked source for the details."
                + (f"\nSource: {'; '.join(sources)}." if sources else "")
                + footer)
    if getattr(alert, "kind", "threshold") == "trend":
        detail = (
            f"Sensor {alert.sensor_id} shows {alert.field} rising +{alert.value}% "
            f"over its record (trigger: +{alert.threshold}%)."
        )
    else:
        detail = (
            f"Sensor {alert.sensor_id} reported {alert.field} = {alert.value} "
            f"(threshold {alert.threshold}) at {alert.ts} UTC."
        )
    return detail + footer


class Notifier(abc.ABC):
    channel: str

    @abc.abstractmethod
    def send(self, alert) -> None:
        """Deliver one alert (raise on failure)."""


class LogNotifier(Notifier):
    """Logs the alert. Always available; used for dev and dry runs."""

    channel = "log"

    def send(self, alert) -> None:
        log.warning("ALERT %s", alert_subject(alert))


def make_notifier(channel: str) -> Notifier:
    """Build a notifier for a channel, reading config from the environment."""
    if channel == "log":
        return LogNotifier()
    if channel == "email":
        from airwv.notify.email import EmailNotifier

        return EmailNotifier.from_env()
    if channel == "webhook":
        from airwv.notify.webhook import WebhookNotifier

        return WebhookNotifier()
    raise ValueError(f"unknown notify channel {channel!r}")
