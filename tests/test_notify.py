"""Slack/Discord notification fan-out (src/airwv/notify.py) + intake wiring."""
import httpx
import respx
from fastapi.testclient import TestClient

from airwv.notify.chat import ChatNotifier
from airwv.storage import Store
from airwv.web.app import create_app

SLACK = "https://hooks.slack.com/services/T/B/xxx"
DISCORD = "https://discord.com/api/webhooks/1/yyy"


def test_disabled_when_no_webhooks():
    n = ChatNotifier()
    assert n.enabled is False


@respx.mock
def test_send_uses_each_platform_dialect():
    slack = respx.post(SLACK).mock(return_value=httpx.Response(200))
    discord = respx.post(DISCORD).mock(return_value=httpx.Response(204))
    n = ChatNotifier(slack_url=SLACK, discord_url=DISCORD, public_url="https://air.createwv.org")
    n.send("Title", ["> body"], link_label="admin", link_url="https://air.createwv.org/admin")
    assert n.enabled is True
    s = slack.calls.last.request.content.decode()
    d = discord.calls.last.request.content.decode()
    assert '"text"' in s and "*Title*" in s and "<https://air.createwv.org/admin|admin>" in s  # Slack
    assert '"content"' in d and "**Title**" in d and "[admin](https://air.createwv.org/admin)" in d  # Discord


@respx.mock
def test_report_notification_shape():
    route = respx.post(DISCORD).mock(return_value=httpx.Response(204))
    n = ChatNotifier(discord_url=DISCORD)
    n.notify_report(domain="air", category="odor", description="smoke by the river",
                    stage="held", lat=38.35, lon=-81.63)
    body = route.calls.last.request.content.decode()
    assert "New air report" in body and "odor" in body
    assert "held for review" in body and "38.350" in body  # real coords in the private channel
    assert '"username":"AirWV Reports"' in body            # per-message Discord identity


@respx.mock
def test_discord_identity_and_avatar():
    route = respx.post(DISCORD).mock(return_value=httpx.Response(204))
    n = ChatNotifier(discord_url=DISCORD, avatar_url="https://air.createwv.org/static/favicon.png")
    n.send("t", ["x"], username="AirWV")
    body = route.calls.last.request.content.decode()
    assert '"username":"AirWV"' in body
    assert "favicon.png" in body       # avatar_url on the payload


@respx.mock
def test_env_username_overrides_per_message():
    route = respx.post(DISCORD).mock(return_value=httpx.Response(204))
    n = ChatNotifier(discord_url=DISCORD, username="Empower WV")   # global override
    n.notify_report(domain="air", category="", description="d", stage="held", lat=1.0, lon=2.0)
    assert '"username":"Empower WV"' in route.calls.last.request.content.decode()


def test_webhook_failure_never_raises():
    # unroutable host -> httpx raises internally; _post must swallow it
    ChatNotifier(slack_url="http://127.0.0.1:9/hook").send("x", ["y"])


@respx.mock
def test_intake_fires_background_notification(tmp_path):
    route = respx.post(DISCORD).mock(return_value=httpx.Response(204))
    store = Store(f"sqlite:///{tmp_path/'n.sqlite'}")
    store.create_schema()
    # env picked up by notifier_from_env() at create_app time
    import os
    os.environ["AIRWV_DISCORD_WEBHOOK_URL"] = DISCORD
    try:
        client = TestClient(create_app(store))
        r = client.post("/api/reports", json={"domain": "air", "description": "odor downtown",
                                              "lat": 38.35, "lon": -81.63})
        assert r.status_code == 200
    finally:
        del os.environ["AIRWV_DISCORD_WEBHOOK_URL"]
    assert route.called   # TestClient runs background tasks before returning
    assert "New air report" in route.calls.last.request.content.decode()
