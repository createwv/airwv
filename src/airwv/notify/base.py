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


def alert_subject(alert) -> str:
    return f"AirWV alert: {alert.field} {alert.value} ≥ {alert.threshold} at sensor {alert.sensor_id}"


def alert_body(alert) -> str:
    return (
        f"Sensor {alert.sensor_id} reported {alert.field} = {alert.value} "
        f"(threshold {alert.threshold}) at {alert.ts} UTC.\n\n"
        f"— AirWV, West Virginia community air monitoring"
    )


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
