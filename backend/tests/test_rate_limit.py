"""Per-bucket request ceilings.

Run against a fake Redis rather than a real one. The logic under test is the
middleware's — which bucket a path lands in, when it trips, that it fails open —
and none of that is made more true by a live Redis round trip. A fake also makes
the counter deterministic, which a shared Redis would not be.

The bucket assertions are the valuable ones. The whole point of the feature is
that Plaid and SEC routes are throttled far harder than everything else, and a
regression there is invisible: the limiter still "works", it just stops
protecting the two things that actually cost money and IP bans.
"""

from __future__ import annotations

import pytest
import redis
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.main import app
from tests.test_api_key import KEY

AUTH = {"X-API-Key": KEY}


class FakeRedis:
    """Enough of the redis client for the limiter, with a controllable failure."""

    def __init__(self, fail: bool = False):
        self.counts: dict[str, int] = {}
        self.fail = fail

    # The limiter batches INCR + EXPIRE; the pipeline object is the fake itself
    # and `execute` returns the incremented value in the shape it expects.
    def pipeline(self):
        self._pending: list[str] = []
        return self

    def incr(self, key):
        if self.fail:
            raise redis.RedisError("boom")
        self._pending = [key]

    def expire(self, key, seconds, nx=False):
        pass

    def execute(self):
        if self.fail:
            raise redis.RedisError("boom")
        key = self._pending[0]
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key]]

    def ttl(self, key):
        return 30


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "redis_client", fake)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "api_key", KEY)
    return fake


@pytest.fixture
def client():
    """`raise_server_exceptions=False` because these tests deliberately let
    requests through the limiter, and what is behind it needs a database this
    suite does not have. The handler raising a connection error is fine — it
    proves the request was *not* throttled, which is the assertion. Letting the
    exception propagate would just mask the status code being checked.
    """
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Bucket selection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "path,expected_bucket,expected_setting",
    [
        ("/api/v1/plaid/sync", "/api/v1/plaid", "rate_limit_plaid_per_minute"),
        ("/api/v1/plaid/link-token", "/api/v1/plaid", "rate_limit_plaid_per_minute"),
        # More specific prefix must win over the broader /api/v1/plaid one.
        ("/api/v1/plaid/webhook", "/api/v1/plaid/webhook", "rate_limit_webhook_per_minute"),
        ("/api/v1/research/AAPL", "/api/v1/research", "rate_limit_research_per_minute"),
        ("/api/v1/transactions", "default", "rate_limit_default_per_minute"),
        ("/api/v1/accounts", "default", "rate_limit_default_per_minute"),
    ],
)
def test_bucket_selection(path, expected_bucket, expected_setting):
    bucket, limit = rate_limit._bucket_for(path)
    assert bucket == expected_bucket
    assert limit == getattr(settings, expected_setting)


def test_plaid_is_throttled_far_harder_than_the_default_bucket():
    """The cost shield only means something if it is materially tighter."""
    _, plaid_limit = rate_limit._bucket_for("/api/v1/plaid/sync")
    _, default_limit = rate_limit._bucket_for("/api/v1/transactions")
    assert plaid_limit < default_limit / 5


# --------------------------------------------------------------------------- #
# Enforcement
# --------------------------------------------------------------------------- #

def test_plaid_bucket_trips_at_its_limit(client, fake_redis):
    limit = settings.rate_limit_plaid_per_minute
    seen = [
        client.post("/api/v1/plaid/link-token", headers=AUTH).status_code
        for _ in range(limit + 3)
    ]

    # Requests up to the limit are not throttled. They may still fail for other
    # reasons (no database in this suite), so the assertion is specifically that
    # they are not 429 — not that they succeeded.
    assert 429 not in seen[:limit]
    assert seen[limit:] == [429, 429, 429]


def test_429_explains_the_limit_and_sets_retry_after(client, fake_redis):
    limit = settings.rate_limit_plaid_per_minute
    for _ in range(limit):
        client.post("/api/v1/plaid/link-token", headers=AUTH)

    response = client.post("/api/v1/plaid/link-token", headers=AUTH)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert str(limit) in response.json()["detail"]


def test_buckets_are_counted_independently(client, fake_redis):
    """Exhausting Plaid must not throttle the rest of the app."""
    for _ in range(settings.rate_limit_plaid_per_minute + 2):
        client.post("/api/v1/plaid/link-token", headers=AUTH)

    assert client.post("/api/v1/plaid/link-token", headers=AUTH).status_code == 429
    # A different bucket, nowhere near its own ceiling.
    assert client.get("/api/v1/accounts", headers=AUTH).status_code != 429


def test_unauthenticated_requests_are_still_counted(client, fake_redis):
    """The limiter sits outside the auth gate.

    Otherwise anyone could spend the server's time generating 401s forever, and
    the webhook — exempt from the key by necessity — would have no ceiling.
    """
    limit = settings.rate_limit_plaid_per_minute
    seen = [
        client.post("/api/v1/plaid/link-token").status_code for _ in range(limit + 2)
    ]

    assert seen[0] == 401, "expected the auth gate to reject these"
    assert seen[-1] == 429, "expected the limiter to take over once the bucket emptied"


def test_health_is_never_throttled(client, fake_redis):
    for _ in range(settings.rate_limit_default_per_minute + 20):
        assert client.get("/health").status_code == 200


def test_preflight_is_never_throttled(client, fake_redis):
    """A throttled preflight breaks the app for a browser that is behaving."""
    for _ in range(settings.rate_limit_plaid_per_minute + 5):
        response = client.options(
            "/api/v1/plaid/sync",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code != 429


def test_redis_outage_fails_open(client, monkeypatch):
    """Redis down must not take the API down with it."""
    monkeypatch.setattr(rate_limit, "redis_client", FakeRedis(fail=True))
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "api_key", KEY)

    # Reaches the auth gate rather than erroring in the limiter.
    assert client.get("/api/v1/accounts").status_code == 401


def test_disabling_the_limiter_bypasses_redis_entirely(client, monkeypatch):
    monkeypatch.setattr(rate_limit, "redis_client", FakeRedis(fail=True))
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "api_key", KEY)

    assert client.get("/api/v1/accounts").status_code == 401
