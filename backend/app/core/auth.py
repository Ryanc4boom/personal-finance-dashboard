"""A single shared secret, required on every request.

This is not user authentication and is not trying to be. There is still exactly
one user (`services/users.py`), and this gate says nothing about *who* is
calling — only that the caller possesses a secret the frontend was given. It
exists because "no auth at all" made three other defences meaningless:

  * Object-level ownership checks are real, but nothing has to prove it is the
    owner, so the only thing keeping one user's rows away from another is that
    there is one user.
  * Rate limits scoped per caller are pointless when callers are anonymous and
    interchangeable.
  * A CSRF token protects an ambient credential, and there was none.

The mechanism that does the work is the header itself, not its value. A custom
header disqualifies a request from being a CORS "simple request", so the browser
must preflight it, and the preflight fails for any origin not in CORS_ORIGINS.
That is what closes the hole in `/api/v1/plaid/sync`: it takes an optional body,
so an empty POST with a form or `text/plain` content-type used to skip preflight
altogether and run a real, billed Plaid sync on behalf of whatever page sent it.
CORS never blocked that — it only blocked reading the response, long after the
sync had happened.

The key is served to a browser and is therefore not secret *from the user*.
It does not need to be. It is secret from third-party origins, which cannot read
it (same-origin policy protects the frontend bundle) and cannot preflight past
CORS without it.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)

HEADER = "X-API-Key"

# Paths that must stay reachable without the key, kept as small as possible.
EXEMPT_PATHS = frozenset(
    {
        # Liveness. Discloses only {"status", "plaid_env"} and is the first thing
        # to check when the gate itself is misconfigured — requiring the key
        # here would make a 503 indistinguishable from a dead process.
        "/health",
        # Plaid calls this one, and Plaid has no way to learn our key. It is not
        # unprotected: it verifies an ES256 signature over the exact request
        # body (services/plaid_webhook.py), which is a strictly stronger check
        # than a shared secret. Requiring the key here would simply break
        # webhooks.
        "/api/v1/plaid/webhook",
    }
)


def _is_exempt(request: Request) -> bool:
    # CORS preflights carry no custom headers by definition — the browser sends
    # them precisely to ask whether the custom header is allowed. Rejecting the
    # preflight would deny every legitimate cross-origin call before it is made.
    # This is safe: OPTIONS reaches no handler and changes no state.
    if request.method == "OPTIONS":
        return True
    return request.url.path.rstrip("/") in EXEMPT_PATHS or request.url.path in EXEMPT_PATHS


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject any request that does not carry the shared secret."""

    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request):
            return await call_next(request)

        expected = settings.api_key
        if not expected:
            # Fail closed. An unset key is a misconfiguration, and the safe
            # reading of "no key configured" is "let nobody in", not "let
            # everybody in" — this endpoint serves real bank data.
            logger.error("API_KEY is not set; refusing all API requests")
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "API_KEY is not configured on the server. Generate one with "
                        "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"` "
                        "and set API_KEY in .env (backend) and NEXT_PUBLIC_API_KEY in "
                        "frontend/.env.local, then restart both."
                    )
                },
            )

        presented = request.headers.get(HEADER)
        # compare_digest on both the missing and mismatched paths so the two are
        # indistinguishable by timing, and so a short key cannot be narrowed
        # down a character at a time.
        if presented is None or not hmac.compare_digest(presented, expected):
            logger.warning(
                "Rejected unauthenticated %s %s", request.method, request.url.path
            )
            return JSONResponse(
                status_code=401,
                content={"detail": f"Missing or invalid {HEADER}"},
            )

        return await call_next(request)
