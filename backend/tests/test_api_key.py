"""The shared-secret gate in `app.core.auth`.

The assertions that matter here are the negative ones. A gate that accepts
everything passes any test that only checks the happy path, and this one guards
an API serving real bank transactions.

Two tests are regression locks rather than feature tests, and both encode a bug
that was actually reachable before the gate existed:

  * `test_csrf_...` — `/api/v1/plaid/sync` takes an optional body, so an empty
    POST with a non-JSON content-type is a CORS "simple request", skips
    preflight, and used to reach the handler and run a real, billed Plaid sync.
  * `test_rejection_carries_cors_headers` — a 401 raised outside CORSMiddleware
    has no allow-origin header, and the browser then reports "failed to fetch"
    instead of the status. That turns a missing key into what looks like a CORS
    or network fault.

No database is required: every request here is refused by the middleware before
it reaches a handler, and the two exempt paths (`/health`, the Plaid webhook)
touch no session either.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import auth
from app.core.config import settings
from app.main import app

KEY = "test-key-do-not-use-in-anger"
ORIGIN = "http://localhost:3000"


@pytest.fixture(autouse=True)
def _configure_key(monkeypatch):
    """Give the gate a known key for every test unless one overrides it."""
    monkeypatch.setattr(settings, "api_key", KEY)


@pytest.fixture
def client():
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

def test_request_without_key_is_refused(client):
    response = client.get("/api/v1/accounts")
    assert response.status_code == 401
    assert auth.HEADER in response.json()["detail"]


def test_request_with_wrong_key_is_refused(client):
    response = client.get("/api/v1/accounts", headers={auth.HEADER: "wrong"})
    assert response.status_code == 401


def test_prefix_of_valid_key_is_refused(client):
    """A truncated key must not pass — guards against a prefix/startswith compare."""
    response = client.get("/api/v1/accounts", headers={auth.HEADER: KEY[:-1]})
    assert response.status_code == 401


def test_empty_key_header_is_refused(client):
    response = client.get("/api/v1/accounts", headers={auth.HEADER: ""})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/transactions"),
        ("get", "/api/v1/budgets"),
        ("get", "/api/v1/goals"),
        ("get", "/api/v1/investments/holdings"),
        ("get", "/api/v1/net-worth/history"),
        ("post", "/api/v1/plaid/sync"),
        ("post", "/api/v1/plaid/link-token"),
        ("post", "/api/v1/plaid/set-access-token"),
        ("get", "/api/v1/research/AAPL"),
        # The interactive docs describe every route and schema in the app. Not
        # secret, but no reason to hand the map out either.
        ("get", "/docs"),
        ("get", "/openapi.json"),
    ],
)
def test_every_route_is_gated(client, method, path):
    """Blanket coverage: the gate is middleware, so nothing may slip past it.

    Parameterised over routes rather than asserted once, because the failure
    mode being guarded against is a *single* route escaping the gate.
    """
    response = getattr(client, method)(path)
    assert response.status_code == 401, f"{method.upper()} {path} was not gated"


def test_unset_key_fails_closed(client, monkeypatch):
    """No key configured must deny everyone, not admit everyone."""
    monkeypatch.setattr(settings, "api_key", "")
    response = client.get("/api/v1/accounts", headers={auth.HEADER: "anything"})
    assert response.status_code == 503
    # The message has to be actionable — this is the state a fresh clone is in.
    assert "API_KEY" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The CSRF vector the gate exists to close
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "content_type",
    ["application/x-www-form-urlencoded", "text/plain", "multipart/form-data"],
)
def test_csrf_simple_request_to_plaid_sync_is_refused(client, content_type):
    """An empty-body POST with a form/text content-type must not reach the handler.

    These three content types are exactly the set a browser will send
    cross-origin *without* a preflight. `/api/v1/plaid/sync` accepts an optional
    body, so an empty one used to deserialise to `None` and trigger a real sync
    against Plaid — billed, rate-limited by Plaid, and reachable from any page
    the user happened to have open. Requiring a custom header makes the request
    non-simple, so the browser must preflight and CORS gets a say.
    """
    response = client.post(
        "/api/v1/plaid/sync",
        content=b"",
        headers={"Content-Type": content_type, "Origin": "https://evil.example"},
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Exemptions
# --------------------------------------------------------------------------- #

def test_health_is_reachable_without_key(client):
    """Liveness must not require the key, or a 503 from the gate is unreadable."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_is_reachable_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    assert client.get("/health").status_code == 200


def test_webhook_is_exempt_from_the_key_but_still_verified(client):
    """Plaid cannot know our key, so the webhook is gated by signature instead.

    A 401 here would be ambiguous, so the assertion is on the *body*: it must be
    the webhook verifier talking, not the API-key gate. That distinction is the
    whole point — the route is exempt from one check because it enforces a
    stronger one.
    """
    response = client.post("/api/v1/plaid/webhook", json={"webhook_type": "ITEM"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Webhook verification failed"
    assert auth.HEADER not in response.json()["detail"]


def test_preflight_is_not_gated(client):
    """The browser cannot put the key on a preflight; refusing it breaks everything."""
    response = client.options(
        "/api/v1/accounts",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": auth.HEADER,
        },
    )
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert auth.HEADER.lower() in allowed


def test_preflight_from_foreign_origin_is_not_allowed(client):
    """The gate only helps if CORS actually refuses the origins it should."""
    response = client.options(
        "/api/v1/plaid/sync",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": auth.HEADER,
        },
    )
    assert "access-control-allow-origin" not in response.headers


# --------------------------------------------------------------------------- #
# Middleware ordering
# --------------------------------------------------------------------------- #

def test_rejection_carries_cors_headers(client):
    """A 401 must still be readable by the browser that provoked it.

    Fails if ApiKeyMiddleware is ever registered *after* CORSMiddleware, which
    would put it outside and strip the allow-origin header from this response.
    """
    response = client.get("/api/v1/accounts", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == ORIGIN
