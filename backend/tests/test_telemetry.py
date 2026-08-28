"""What is allowed to leave the machine when error reporting is on.

The risk telemetry introduces is not that it fails — it is that it works, and
quietly ships balances and Plaid tokens to a third party for months before
anyone reads a payload. So these tests assert on *absence*: given an event
carrying every kind of sensitive value this app handles, nothing sensitive comes
out the other side.

Written against the plain dict Sentry passes to `before_send`, so the suite does
not need sentry-sdk installed or a network stub.
"""

from __future__ import annotations

import pytest

from app.core.telemetry import init_telemetry, redact, scrub_event, template_path
from app.core.config import settings


# --------------------------------------------------------------------------- #
# Redaction of free text
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "text,leaked",
    [
        ("token access-production-9f3b2c1d-aaaa-bbbb", "access-production"),
        ("link-sandbox-1234abcd-ef56 expired", "link-sandbox"),
        ("public-development-deadbeef-0000 exchange failed", "public-development"),
        ("stored gAAAAABm9Xq3T_kL0pQrStUvWxYz1234567890abcdef=", "gAAAAA"),
        ("balance is $12,481.07 after sync", "12,481.07"),
        ("row amount -1049.99 did not match", "1049.99"),
        ("account 4111111111111111 rejected", "4111111111111111"),
    ],
)
def test_redact_removes_sensitive_values(text, leaked):
    assert leaked not in redact(text)


def test_redact_keeps_the_sentence_readable():
    """Redaction that returns an empty string is the same as no telemetry."""
    out = redact("no category for merchant 'AMZN Mktp' at -$104.99")
    assert "no category for merchant" in out
    assert "[amount]" in out
    assert "104.99" not in out


def test_small_numbers_survive():
    """Over-redacting to the point of uselessness is its own failure. Status
    codes and short counts are not account data."""
    assert redact("got 502 after 3 retries") == "got 502 after 3 retries"


# --------------------------------------------------------------------------- #
# Path templating
# --------------------------------------------------------------------------- #

def test_template_path_replaces_uuids():
    path = "/api/v1/transactions/2b1e4f60-8a0c-4c6e-9f21-77c0d5b3e9aa"
    assert template_path(path) == "/api/v1/transactions/{id}"


def test_template_path_replaces_numeric_segments():
    assert template_path("/api/v1/accounts/4021/history") == "/api/v1/accounts/{id}/history"


def test_template_path_leaves_route_structure_alone():
    """The whole point is that the endpoint stays identifiable."""
    assert template_path("/api/v1/plaid/sync") == "/api/v1/plaid/sync"


# --------------------------------------------------------------------------- #
# Event scrubbing
# --------------------------------------------------------------------------- #

def _event() -> dict:
    """An event carrying one of everything this app must not forward."""
    return {
        "server_name": "ryans-macbook.local",
        "user": {"id": "1", "ip_address": "10.0.0.4", "email": "me@example.com"},
        "extra": {"account_balance_cents": 1_248_107},
        "request": {
            "method": "PATCH",
            "url": "http://localhost:8000/api/v1/transactions/2b1e4f60-8a0c-4c6e-9f21-77c0d5b3e9aa?account_id=abc",
            "query_string": "account_id=abc&start_date=2026-01-01",
            "data": {"access_token": "access-production-9f3b2c1d-aaaa"},
            "cookies": "session=xyz",
            "headers": {"X-API-Key": "s3cr3t-key-value", "Content-Type": "application/json"},
            "env": {"REMOTE_ADDR": "10.0.0.4"},
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "balance 12481.07 does not reconcile for account 4111111111111111",
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "reconcile",
                                "lineno": 88,
                                "vars": {"txn": "<Transaction amount_cents=-10499>"},
                            }
                        ]
                    },
                }
            ]
        },
        "logentry": {
            "message": "sync failed for item %s",
            "params": ["access-production-9f3b2c1d-aaaa"],
        },
        "breadcrumbs": {
            "values": [
                {
                    "category": "query",
                    "message": "SELECT amount_cents FROM transactions WHERE id = 4021",
                    "data": {"db.params": ["4021"]},
                }
            ]
        },
    }


@pytest.fixture
def scrubbed() -> dict:
    return scrub_event(_event())


def test_no_sensitive_value_survives_anywhere(scrubbed):
    """The blunt assertion, and the one that catches a section added later that
    nobody thought to scrub: serialise the whole event and look for the values."""
    blob = repr(scrubbed)
    for secret in (
        "s3cr3t-key-value",
        "access-production",
        "12481.07",
        "4111111111111111",
        "session=xyz",
        "10.0.0.4",
        "me@example.com",
        "ryans-macbook",
        "1248107",
        "10499",
    ):
        assert secret not in blob, f"{secret!r} was forwarded"


def test_identifying_context_is_kept(scrubbed):
    """Scrubbing that removes the ability to locate the fault is not a win."""
    assert scrubbed["request"]["method"] == "PATCH"
    assert scrubbed["request"]["url"].endswith("/api/v1/transactions/{id}")
    assert scrubbed["exception"]["values"][0]["type"] == "ValueError"
    assert scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]["function"] == "reconcile"


def test_request_body_query_and_headers_are_dropped_wholesale(scrubbed):
    """Dropped as sections rather than filtered key by key — a deny-list needs
    editing every time a schema grows a field, and forgetting is silent."""
    for key in ("data", "cookies", "headers", "env", "query_string"):
        assert key not in scrubbed["request"]


def test_frame_locals_are_dropped(scrubbed):
    """A frame in the ingestion pipeline holds the whole transaction row."""
    assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_log_params_are_dropped(scrubbed):
    """`params` hold the unformatted interpolation arguments — the raw values,
    before they were rendered into the message."""
    assert "params" not in scrubbed["logentry"]


def test_scrubbing_an_empty_event_does_not_explode():
    """before_send receives whatever the SDK built; missing sections are normal."""
    assert scrub_event({}) == {}


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

def test_disabled_without_a_dsn(monkeypatch):
    monkeypatch.setattr(settings, "sentry_dsn", "")
    assert init_telemetry() is False


def test_a_missing_sdk_does_not_take_the_app_down(monkeypatch):
    """An observability tool that can refuse to start the API is worse than no
    observability."""
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@example.invalid/1")
    monkeypatch.setitem(__import__("sys").modules, "sentry_sdk", None)
    assert init_telemetry() is False


def test_tracing_is_off_by_default():
    """Spans carry SQL statements and their bound parameters."""
    assert settings.sentry_traces_sample_rate == 0.0
