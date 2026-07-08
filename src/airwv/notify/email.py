"""Email notifier (SMTP).

Configuration comes from the environment — set these when you're ready to send;
until then the ``log`` channel works for testing. Nothing here is committed.

    AIRWV_SMTP_HOST       smtp server host
    AIRWV_SMTP_PORT       port (default 587)
    AIRWV_SMTP_USER       username
    AIRWV_SMTP_PASSWORD   password / app token
    AIRWV_SMTP_FROM       From: address (default = user)
    AIRWV_SMTP_TLS        "1" to use STARTTLS (default "1")
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from airwv.notify.base import Notifier, alert_body, alert_subject


class EmailConfigError(RuntimeError):
    pass


@dataclass
class EmailNotifier(Notifier):
    host: str
    port: int
    user: str
    password: str
    sender: str
    use_tls: bool = True
    channel: str = "email"

    @classmethod
    def from_env(cls) -> "EmailNotifier":
        host = os.environ.get("AIRWV_SMTP_HOST", "").strip()
        user = os.environ.get("AIRWV_SMTP_USER", "").strip()
        password = os.environ.get("AIRWV_SMTP_PASSWORD", "").strip()
        if not (host and user and password):
            raise EmailConfigError(
                "SMTP is not configured. Set AIRWV_SMTP_HOST/USER/PASSWORD "
                "(and optionally PORT/FROM/TLS) to send email alerts."
            )
        return cls(
            host=host,
            port=int(os.environ.get("AIRWV_SMTP_PORT", "587")),
            user=user,
            password=password,
            sender=os.environ.get("AIRWV_SMTP_FROM", "").strip() or user,
            use_tls=os.environ.get("AIRWV_SMTP_TLS", "1") != "0",
        )

    def send(self, alert) -> None:
        msg = EmailMessage()
        msg["Subject"] = alert_subject(alert)
        msg["From"] = self.sender
        msg["To"] = alert.target
        msg.set_content(alert_body(alert))
        with smtplib.SMTP(self.host, self.port) as smtp:
            if self.use_tls:
                smtp.starttls()
            smtp.login(self.user, self.password)
            smtp.send_message(msg)
