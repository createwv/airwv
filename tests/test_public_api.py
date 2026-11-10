"""CORS and the per-IP rate limit on /api/* and /data/dumps/* (web/public_api.py)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from airwv.storage import Store
from airwv.web.app import create_app
from airwv.web.public_api import PublicDataMiddleware

ORIGIN = {"Origin": "https://partner.example"}


def _client(limits=None, clock=None, host="203.0.113.9"):
    """A tiny app behind the middleware, so counting is exact and the clock can be moved."""
    app = FastAPI()
    kw = {"clock": clock} if clock else {}
    app.add_middleware(PublicDataMiddleware, limits=limits or {"api": 2, "dumps": 1}, **kw)
    for path in ("/api/x", "/api/admin/x", "/data/dumps/x", "/data", "/"):
        app.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
    app.add_api_route("/api/x", lambda: {"ok": True}, methods=["POST"])
    return TestClient(app, client=(host, 50000))


def test_read_responses_are_open_to_any_origin():
    c = _client()
    r = c.get("/api/x", headers=ORIGIN)
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers
    assert c.get("/data/dumps/x", headers=ORIGIN).headers["access-control-allow-origin"] == "*"


def test_writes_admin_and_pages_get_no_cors():
    c = _client({"api": 50, "dumps": 50})
    assert "access-control-allow-origin" not in c.post("/api/x", headers=ORIGIN).headers
    assert "access-control-allow-origin" not in c.get("/api/admin/x", headers=ORIGIN).headers
    assert "access-control-allow-origin" not in c.get("/data", headers=ORIGIN).headers


def test_preflight_allows_reads_only():
    c = _client()
    ok = c.options("/api/x", headers={**ORIGIN, "Access-Control-Request-Method": "GET"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == "*"
    assert "POST" not in ok.headers["access-control-allow-methods"]
    for path, method in (("/api/x", "POST"), ("/api/admin/x", "GET")):
        r = c.options(path, headers={**ORIGIN, "Access-Control-Request-Method": method})
        assert r.status_code == 405 and "access-control-allow-origin" not in r.headers


def test_limit_returns_429_with_retry_after_and_cors_then_resets():
    now = [100.0]
    c = _client(clock=lambda: now[0])
    assert [c.get("/api/x").status_code for _ in range(2)] == [200, 200]
    now[0] += 20
    r = c.get("/api/x", headers=ORIGIN)
    assert r.status_code == 429 and r.headers["retry-after"] == "40"
    assert r.headers["access-control-allow-origin"] == "*"   # so a browser client can read it
    now[0] += 40
    assert c.get("/api/x").status_code == 200


def test_buckets_and_clients_count_separately():
    c = _client()
    assert c.get("/data/dumps/x").status_code == 200
    assert c.get("/data/dumps/x").status_code == 429
    assert c.get("/api/x").status_code == 200              # dumps budget is its own
    a, b = {"CF-Connecting-IP": "198.51.100.1"}, {"CF-Connecting-IP": "198.51.100.2"}
    assert [c.get("/data/dumps/x", headers=a).status_code for _ in range(2)] == [200, 429]
    assert c.get("/data/dumps/x", headers=b).status_code == 200


def test_ips_are_not_kept_raw():
    c = _client()
    c.get("/api/x", headers={"CF-Connecting-IP": "198.51.100.1"})
    limiter = c.app.middleware_stack
    while not isinstance(limiter, PublicDataMiddleware):
        limiter = limiter.app
    assert limiter.counts and not any("198.51.100.1" in ip for _, ip in limiter.counts)


def test_admin_and_pages_are_not_limited():
    c = _client({"api": 1, "dumps": 1})
    assert {c.get("/api/admin/x").status_code for _ in range(3)} == {200}
    assert {c.get("/data").status_code for _ in range(3)} == {200}


def test_direct_local_requests_are_exempt_but_proxied_ones_are_not():
    c = _client({"api": 1, "dumps": 1}, host="127.0.0.1")
    assert {c.get("/api/x").status_code for _ in range(3)} == {200}
    proxied = {"X-Forwarded-For": "127.0.0.1"}   # a spoofed loopback through the proxy still counts
    assert [c.get("/api/x", headers=proxied).status_code for _ in range(2)] == [200, 429]


def test_zero_turns_a_limit_off():
    c = _client({"api": 0, "dumps": 1})
    assert {c.get("/api/x").status_code for _ in range(5)} == {200}


def test_real_app_wires_limits_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRWV_RATE_LIMIT_API", "1")
    store = Store(f"sqlite:///{tmp_path / 'p.sqlite'}")
    store.create_schema()
    c = TestClient(create_app(store))
    first = c.get("/api/guide", headers=ORIGIN)
    assert first.status_code == 200 and first.headers["access-control-allow-origin"] == "*"
    assert c.get("/api/guide").status_code == 429
    assert c.get("/").status_code == 200                     # pages aren't limited
