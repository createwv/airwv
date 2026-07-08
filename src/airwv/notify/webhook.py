"""Webhook notifier — POSTs the alert as JSON to the subscription's target URL.

Lets partners pipe alerts into Slack/Discord/their own systems.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

import httpx

from airwv.notify.base import Notifier, alert_subject


class WebhookNotifier(Notifier):
    channel = "webhook"

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    def send(self, alert) -> None:
        payload = asdict(alert) if is_dataclass(alert) else dict(alert)
        payload["ts"] = str(payload.get("ts"))
        payload["text"] = alert_subject(alert)
        resp = httpx.post(alert.target, json=payload, timeout=self._timeout)
        resp.raise_for_status()
