"""CORS and a per-IP rate limit for the public data (``/api/*``, ``/data/dumps/*``).

CORS is read-only and open (``*``, no credentials): GET/HEAD responses can be read by any
site, so partners can build widgets on the data. Writes (reports, feedback, sign-ups) get no
CORS headers, and ``/api/admin/*`` is left out of both CORS and the limit.

The limit is a fixed one-minute window per client, kept in memory: it resets on restart and
assumes one process (``python -m airwv.web`` runs one uvicorn worker). Clients are counted by
``ip_hash``, never the raw IP. Direct local requests (the product harness, a dev server) don't
count; anything that came through the proxy carries a forwarded header and does.
"""

import math
import time

from fastapi.responses import JSONResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.requests import Request

from airwv.reporting import client_ip, ip_hash

WINDOW_SECONDS = 60
READ_METHODS = ("GET", "HEAD")
LOOPBACK = ("127.0.0.1", "::1")
# A constant header, not an echo of Origin, so Cloudflare can cache one copy without Vary
CORS_HEADERS = {"Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "Retry-After"}
PREFLIGHT_HEADERS = {**CORS_HEADERS, "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                     "Access-Control-Allow-Headers": "*", "Access-Control-Max-Age": "86400"}


def _bucket(path: str) -> str | None:
    """Which limit a path falls under; None for everything this middleware leaves alone."""
    if path.startswith("/api/admin/"):
        return None
    if path.startswith("/api/"):
        return "api"
    if path.startswith("/data/dumps/"):
        return "dumps"
    return None


def _is_direct_local(request: Request) -> bool:
    h = request.headers
    return (request.client is not None and request.client.host in LOOPBACK
            and "cf-connecting-ip" not in h and "x-forwarded-for" not in h)


class PublicDataMiddleware:
    """``limits`` is requests per minute per client for each bucket (0 turns that limit off)."""

    def __init__(self, app, limits: dict[str, int], clock=time.monotonic):
        self.app = app
        self.limits = limits
        self.clock = clock
        self.window_start = clock()
        self.counts: dict[tuple[str, str], int] = {}

    def _retry_after(self, bucket: str, request: Request) -> int | None:
        """Count this request; seconds until the next window if it's over the limit, else None."""
        limit = self.limits[bucket]
        if not limit or _is_direct_local(request):
            return None
        now = self.clock()
        if now - self.window_start >= WINDOW_SECONDS:   # dropping the old window keeps this bounded
            self.window_start = now
            self.counts.clear()
        key = (bucket, ip_hash(client_ip(request)))
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] <= limit:
            return None
        return max(1, math.ceil(WINDOW_SECONDS - (now - self.window_start)))

    async def __call__(self, scope, receive, send):
        bucket = _bucket(scope["path"]) if scope["type"] == "http" else None
        if bucket is None:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if (scope["method"] == "OPTIONS"
                and request.headers.get("access-control-request-method") in READ_METHODS):
            await Response(status_code=204, headers=PREFLIGHT_HEADERS)(scope, receive, send)
            return
        cors = CORS_HEADERS if scope["method"] in READ_METHODS else {}
        retry_after = self._retry_after(bucket, request)
        if retry_after is not None:
            over = JSONResponse({"detail": "Too many requests. Please slow down and try again shortly."},
                                status_code=429, headers={**cors, "Retry-After": str(retry_after)})
            await over(scope, receive, send)
            return

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(cors)
            await send(message)

        await self.app(scope, receive, send_with_cors)
