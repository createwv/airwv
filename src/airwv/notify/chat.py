"""Chat pings to Slack / Discord incoming webhooks for new reports & feedback.

Distinct from the subscriber *alert* channels in this package (log/email/webhook,
see ``base.py``): this is a maintainer-facing heads-up so nobody has to poll
``/admin``. Both platforms accept a plain JSON POST to a per-channel *incoming
webhook* URL — no bot, OAuth, or API key needed:

    Slack:   {"text": "..."}      https://api.slack.com/messaging/webhooks
    Discord: {"content": "..."}   Server → Integrations → Webhooks

Configure via environment (either, both, or neither):

    AIRWV_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxxx
    AIRWV_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/xxxx
    AIRWV_PUBLIC_URL=https://air.createwv.org   # for the "review in admin" link

When neither webhook is set, notifications are silently disabled. Delivery is
best-effort: it runs in a background task and never raises into the request path,
so a slow or broken webhook can never fail or delay a user's submission.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("airwv.notify")

# short, private-channel-friendly labels per reporting domain
DOMAIN_EMOJI = {
    "air": "💨", "water": "💧", "soil": "🌱",
    "wildlife": "🦌", "violation": "⚠️", "other": "📣",
}


def _clip(text: str, limit: int = 280) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[: limit - 1] + "…" if len(text) > limit else text


class ChatNotifier:
    """Fan a short message out to whichever Slack/Discord webhooks are configured."""

    def __init__(self, slack_url: str = "", discord_url: str = "",
                 public_url: str = "", username: str = "", avatar_url: str = "",
                 timeout: float = 5.0) -> None:
        self.slack_url = (slack_url or "").strip()
        self.discord_url = (discord_url or "").strip()
        self.public_url = (public_url or "").strip().rstrip("/")
        # Discord identity overrides. `username` (if set) forces ONE name for every
        # message; otherwise each message uses its own default ("AirWV Reports" etc).
        self.username = (username or "").strip()
        self.avatar_url = (avatar_url or "").strip()
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.slack_url or self.discord_url)

    @property
    def admin_link(self) -> str:
        return f"{self.public_url}/admin" if self.public_url else ""

    def _post(self, url: str, payload: dict) -> None:
        try:
            httpx.post(url, json=payload, timeout=self.timeout)
        except Exception as exc:  # best-effort — never propagate into the request
            log.warning("chat webhook failed: %s", exc)

    def send(self, title: str, lines: list[str] | None = None,
             link_label: str = "", link_url: str = "", username: str = "") -> None:
        """Post ``title`` + ``lines`` (+ optional link) to each configured webhook,
        using each platform's markdown dialect. ``username`` sets the Discord
        display name for this message (a global env override wins if set)."""
        lines = lines or []
        if self.slack_url:
            text = f"*{title}*"
            if lines:
                text += "\n" + "\n".join(lines)
            if link_url:
                text += f"\n<{link_url}|{link_label or link_url}>"   # Slack link syntax
            self._post(self.slack_url, {"text": text})
        if self.discord_url:
            text = f"**{title}**"
            if lines:
                text += "\n" + "\n".join(lines)
            if link_url:
                text += f"\n[{link_label or link_url}]({link_url})"  # Discord/Markdown link
            payload = {"content": text}
            name = self.username or username     # env override wins, else per-message
            if name:
                payload["username"] = name
            if self.avatar_url:
                payload["avatar_url"] = self.avatar_url
            self._post(self.discord_url, payload)

    def notify_report(self, *, domain: str, category: str, description: str,
                      stage: str, lat: float, lon: float) -> None:
        emoji = DOMAIN_EMOJI.get(domain, "📣")
        title = f"{emoji} New {domain} report" + (f" — {category}" if category else "")
        lines = []
        desc = _clip(description)
        if desc:
            lines.append("> " + desc)
        flag = "🚩 held for review" if stage == "held" else "✅ published (unverified)"
        lines.append(f"{flag} · {lat:.3f}, {lon:.3f}")   # real coords — private channel
        self.send(title, lines, link_label="Review in admin", link_url=self.admin_link,
                  username="AirWV Reports")

    def notify_feedback(self, *, kind: str, message: str,
                        page: str | None, contact: str | None) -> None:
        title = f"📝 New site feedback — {kind}"
        lines = ["> " + _clip(message)]
        meta = " · ".join(x for x in (page, contact) if x)
        if meta:
            lines.append(meta)
        self.send(title, lines, link_label="Open admin", link_url=self.admin_link,
                  username="AirWV Feedback")


def chat_notifier_from_env() -> ChatNotifier:
    public_url = os.environ.get("AIRWV_PUBLIC_URL", "https://air.createwv.org")
    # square site favicon crops cleanly to Discord's circular avatar
    avatar = os.environ.get("AIRWV_DISCORD_AVATAR_URL", f"{public_url}/static/favicon.png")
    return ChatNotifier(
        slack_url=os.environ.get("AIRWV_SLACK_WEBHOOK_URL", ""),
        discord_url=os.environ.get("AIRWV_DISCORD_WEBHOOK_URL", ""),
        public_url=public_url,
        username=os.environ.get("AIRWV_DISCORD_USERNAME", ""),
        avatar_url=avatar,
    )
