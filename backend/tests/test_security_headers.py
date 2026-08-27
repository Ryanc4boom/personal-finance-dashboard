"""Security response headers, and strict request-body parsing.

Both are the kind of control that is easy to add and easy to silently lose — a
middleware reordering drops the headers from error responses, a new schema
forgets the strict base — and neither failure is visible in normal use. Hence
tests that assert the *absence* of a regression rather than a feature.
"""

from __future__ import annotations

import pydantic
import pytest
from fastapi.testclient import TestClient

from app.core import headers as headers_module
from app.core.config import settings
from app.main import app
from tests.test_api_key import KEY

AUTH = {"X-API-Key": KEY}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "api_key", KEY)


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Headers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,value", sorted(headers_module.HEADERS.items()))
def test_headers_present_on_success(client, name, value):
    response = client.get("/health")
    assert response.headers.get(name) == value


@pytest.mark.parametrize("name", sorted(headers_module.HEADERS))
def test_headers_present_on_auth_rejection(client, name):
    """Error responses need these most — they are the ones most likely to carry
    an unexpected body, and the easiest to leave outside the middleware stack."""
    response = client.get("/api/v1/accounts")
    assert response.status_code == 401
    assert name in response.headers


def test_nosniff_is_set():
    """Called out explicitly because it is the one doing real work here: it
    stops a JSON body containing user-supplied merchant text from being sniffed
    as HTML and executed."""
    assert headers_module.HEADERS["X-Content-Type-Options"] == "nosniff"


def test_csp_denies_everything_by_default():
    csp = headers_module.HEADERS["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_is_not_sent_over_http(client):
    """Sending HSTS from a localhost dev server pins the whole `localhost` host
    in the browser and breaks plain http for every other project on the machine
    until it expires. It must be conditional on the scheme."""
    response = client.get("/health")
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_is_sent_over_https():
    https_client = TestClient(app, base_url="https://testserver")
    response = https_client.get("/health")
    assert response.headers.get("Strict-Transport-Security") == headers_module.HSTS


# --------------------------------------------------------------------------- #
# Strict request bodies
# --------------------------------------------------------------------------- #

REQUEST_SCHEMAS = [
    ("app.schemas.plaid", "SetAccessTokenRequest"),
    ("app.schemas.plaid", "SyncRequest"),
    ("app.schemas.budgets", "BudgetUpsert"),
    ("app.schemas.goals", "GoalCreate"),
    ("app.schemas.goals", "GoalUpdate"),
    ("app.schemas.ledger", "TransactionUpdate"),
    ("app.schemas.recurring", "RecurringStreamUpdate"),
    ("app.schemas.rules", "RuleCreate"),
]


@pytest.mark.parametrize("module_path,name", REQUEST_SCHEMAS)
def test_request_schemas_forbid_extra_fields(module_path, name):
    """Every body-parsed schema rejects unknown keys.

    Enumerated rather than discovered so that adding a request schema without
    the strict base is a failing test rather than a silent omission.
    """
    import importlib

    model = getattr(importlib.import_module(module_path), name)
    assert model.model_config.get("extra") == "forbid", f"{name} accepts extra fields"


def test_unknown_body_field_is_rejected_not_ignored():
    """The concrete failure this prevents: a typo'd or injected field being
    dropped in silence and the request otherwise succeeding."""
    from app.schemas.goals import GoalCreate

    with pytest.raises(pydantic.ValidationError) as exc:
        GoalCreate(
            name="holiday",
            category="savings",
            target_amount_cents=100_00,
            user_id="someone-elses-id",
        )

    # `any`, not `[0]`: pydantic reports every problem it finds, and "category"
    # is a constrained enum whose own error may sort first. The assertion is
    # that the unknown field was rejected, not that it was rejected first.
    errors = exc.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors), errors
    assert any("user_id" in e["loc"] for e in errors), errors


def test_unknown_field_over_http_is_a_422(client):
    response = client.patch(
        "/api/v1/transactions/00000000-0000-0000-0000-000000000000",
        json={"notes": "fine", "account_id": "not-mine"},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert any(e["type"] == "extra_forbidden" for e in response.json()["detail"])
