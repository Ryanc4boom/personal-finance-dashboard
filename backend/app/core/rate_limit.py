"""Per-caller request ceilings, backed by the Redis already in the stack.

Implemented as middleware rather than per-route decorators on purpose. The
requirement is that *every* public route is covered, and a decorator has to be
remembered on each new endpoint — the failure mode is a route silently shipping
unlimited. Middleware is covered by construction, and a new bucket is one entry
in `_BUCKETS` rather than an edit to thirteen routers.

Buckets exist because the routes are not equally expensive. Most cost a database
query. A few cost money or an IP ban:

  * Plaid routes are billed per call against a real quota.
  * Research routes fan out to SEC EDGAR, which enforces its ceiling by banning
    the source IP — a limit breach there takes the feature out entirely, and not
    just for the request that caused it.

`slowapi` was the obvious alternative and would be a reasonable choice. This is a
fixed-window counter over Redis INCR/EXPIRE instead, because it needs no new
dependency, reuses `core/redis_client.py`, and applies globally without touching
route signatures. The tradeoff of a fixed window is honest and worth stating: a
caller can send up to 2x the limit across a window boundary (all of window N at
its end, all of N+1 at its start). For a cost shield on a single-user app that is
immaterial; it would not be for a public API needing a hard guarantee, and a
sliding window log would be the upgrade.
"""

from __future__ import annotations

import logging

import redis
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.redis_client import client as redis_client

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60

# Longest prefix wins, so a more specific path can override a broader one.
# Values name a settings attribute rather than a number, so the limits stay
# configurable and the mapping stays declarative.
_BUCKETS: tuple[tuple[str, str], ...] = (
    ("/api/v1/plaid/webhook", "rate_limit_webhook_per_minute"),
    ("/api/v1/plaid", "rate_limit_plaid_per_minute"),
    ("/api/v1/research", "rate_limit_research_per_minute"),
)

# Liveness probes must not be throttled — a monitor polling /health should never
# be the thing that trips the limiter, and the endpoint costs nothing.
EXEMPT_PATHS = frozenset({"/health"})


def _bucket_for(path: str) -> tuple[str, int]:
    """The bucket name and its per-minute ceiling for `path`."""
    for prefix, setting_name in _BUCKETS:
        if path.startswith(prefix):
            return prefix, getattr(settings, setting_name)
    return "default", settings.rate_limit_default_per_minute


def _caller(request: Request) -> str:
    """Identity for limiting purposes.

    Client IP. With one shared API key there is nothing finer to key on — every
    legitimate caller presents the same secret, so keying on the key itself would
    put the whole app in one bucket.

    X-Forwarded-For is deliberately *not* trusted: it is attacker-controlled
    unless a proxy is known to overwrite it, and honouring it here would let
    anyone reset their own counter by inventing a header. This app is served
    directly by uvicorn today. If a reverse proxy is ever put in front, this is
    the line that has to change, in step with uvicorn's --forwarded-allow-ips.
    """
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        bucket, limit = _bucket_for(request.url.path)
        key = f"ratelimit:{bucket}:{_caller(request)}"

        try:
            # INCR then EXPIRE in one round trip. INCR creates the key at 1 when
            # absent, so the first request of a window is what sets the TTL;
            # setting it unconditionally would slide the window forward on every
            # request and the limit would never actually reset.
            pipe = redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, WINDOW_SECONDS, nx=True)
            count = pipe.execute()[0]
        except redis.RedisError:
            # Fail open. Redis being unavailable should not take the app down,
            # and it already degrades Plaid sync independently (sync_lock needs
            # it), so a hard failure here would add nothing but an outage.
            # Logged at ERROR because running unthrottled is not a normal state.
            logger.error("Rate limiter unavailable (Redis error); allowing request")
            return await call_next(request)

        if count > limit:
            ttl = 0
            try:
                ttl = max(redis_client.ttl(key), 0)
            except redis.RedisError:
                pass
            logger.warning(
                "Rate limit hit: %s %s (bucket=%s, %s/%s)",
                request.method,
                request.url.path,
                bucket,
                count,
                limit,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"Rate limit exceeded: {limit} requests per minute for "
                        f"{bucket}. Retry in {ttl}s."
                    )
                },
                # Standard, and it lets a well-behaved frontend back off instead
                # of retrying straight into the same wall.
                headers={"Retry-After": str(ttl or WINDOW_SECONDS)},
            )

        return await call_next(request)
