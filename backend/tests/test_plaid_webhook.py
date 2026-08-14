"""Rejection tests for Plaid webhook signature verification.

The webhook endpoint is the only route a third party is meant to call, so it
cannot be protected by asking who the caller is — `verify()` is the entire
access control for it. That makes the interesting assertions the negative ones:
a regression here is silent, because a verifier that accepts everything passes
any test that only checks the happy path.

Plaid will not sign arbitrary payloads for us, so these tests stand in for
Plaid: a P-256 keypair is generated per session and published through a stubbed
`_fetch_key`, which also keeps the suite offline.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import jwt
import plaid
import pytest
import redis
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services import plaid_webhook as pw

# Bound before any test can monkeypatch the module attribute. The autouse
# fixture below replaces `pw._fetch_key` for every test, so the key-retrieval
# tests at the bottom — the ones actually exercising it — would otherwise be
# calling the stub.
FETCH_KEY = pw._fetch_key

KID = "test-key-1"
BODY = json.dumps(
    {"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "itm_1"}
).encode()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _segment(obj: dict) -> str:
    return _b64url(json.dumps(obj).encode())


@pytest.fixture(scope="session")
def keypair() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="session")
def jwk(keypair: ec.EllipticCurvePrivateKey) -> dict:
    numbers = keypair.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url(numbers.x.to_bytes(32, "big")),
        "y": _b64url(numbers.y.to_bytes(32, "big")),
        "kid": KID,
        "alg": "ES256",
        "use": "sig",
    }


@pytest.fixture
def expired_kids() -> set[str]:
    return set()


@pytest.fixture(autouse=True)
def stub_key_fetch(monkeypatch, jwk: dict, expired_kids: set[str]) -> None:
    """Publish only our key, and nothing else — no network, no Redis."""

    def fake_fetch(key_id: str) -> dict:
        if key_id in expired_kids:
            raise pw.WebhookVerificationError(f"Webhook key {key_id!r} is expired")
        if key_id != KID:
            raise pw.WebhookVerificationError(
                f"Plaid did not recognise webhook key {key_id!r}"
            )
        return jwk

    monkeypatch.setattr(pw, "_fetch_key", fake_fetch)


@pytest.fixture
def sign(keypair: ec.EllipticCurvePrivateKey):
    """Mint a token the way Plaid would."""

    def _sign(body: bytes = BODY, *, iat: float | None = None, kid: str = KID, key=None) -> str:
        payload = {
            "iat": int(iat if iat is not None else time.time()),
            "request_body_sha256": hashlib.sha256(body).hexdigest(),
        }
        return jwt.encode(payload, key or keypair, algorithm="ES256", headers={"kid": kid})

    return _sign


# --------------------------------------------------------------------------- #
# Accepted
# --------------------------------------------------------------------------- #

def test_valid_signature_is_accepted(sign):
    payload = pw.verify(sign(), BODY)
    assert payload["request_body_sha256"] == hashlib.sha256(BODY).hexdigest()


def test_token_inside_freshness_window_is_accepted(sign):
    assert pw.verify(sign(iat=time.time() - 60), BODY)


def test_unicode_body_digest_matches_raw_bytes(sign):
    """Guards the raw-bytes contract.

    The digest covers the exact bytes on the wire. If a caller ever re-encodes
    the parsed JSON before hashing, a body like this one stops matching —
    `json.dumps` escapes non-ASCII by default, so the re-encoded form differs
    from what was transmitted even though the data is identical.
    """
    body = '{"webhook_type":"ITEM","name":"Caf\u00e9 Münster"}'.encode()
    assert pw.verify(sign(body), body)


# --------------------------------------------------------------------------- #
# Rejected
# --------------------------------------------------------------------------- #

def test_missing_header_is_rejected():
    with pytest.raises(pw.WebhookVerificationError, match="Missing"):
        pw.verify(None, BODY)


def test_malformed_token_is_rejected():
    with pytest.raises(pw.WebhookVerificationError, match="Malformed"):
        pw.verify("not-a-jwt", BODY)


def test_tampered_body_is_rejected(sign):
    """A genuine token replayed against a substituted body."""
    token = sign(BODY)
    attacker_body = json.dumps(
        {"webhook_type": "ITEM", "webhook_code": "ERROR", "item_id": "itm_ATTACKER"}
    ).encode()
    with pytest.raises(pw.WebhookVerificationError, match="does not match signed digest"):
        pw.verify(token, attacker_body)


def test_signature_from_another_key_is_rejected(sign):
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(pw.WebhookVerificationError, match="Signature verification failed"):
        pw.verify(sign(key=attacker_key), BODY)


def test_unknown_key_id_is_rejected(sign):
    with pytest.raises(pw.WebhookVerificationError, match="did not recognise"):
        pw.verify(sign(kid="who-dis"), BODY)


def test_rotated_out_key_is_rejected(sign, expired_kids):
    """Plaid keeps serving retired keys so in-flight webhooks still validate.

    Honouring `expired_at` is the difference between a rotation and a key that
    is trusted forever.
    """
    expired_kids.add(KID)
    with pytest.raises(pw.WebhookVerificationError, match="expired"):
        pw.verify(sign(), BODY)


def test_alg_none_forgery_is_rejected():
    """`alg: none` asks the library to skip verification altogether."""
    token = (
        _segment({"alg": "none", "kid": KID})
        + "."
        + _segment(
            {"iat": int(time.time()), "request_body_sha256": hashlib.sha256(BODY).hexdigest()}
        )
        + "."
    )
    with pytest.raises(pw.WebhookVerificationError, match="Unexpected token algorithm"):
        pw.verify(token, BODY)


def test_hs256_algorithm_confusion_is_rejected(keypair):
    """HS256 signed with the public key as the HMAC secret.

    The EC public key is, by definition, public. A verifier that honours the
    token's own `alg` will treat it as a shared secret and validate anything an
    attacker signs with it. Built by hand because PyJWT refuses to *encode*
    this — an attacker is not using PyJWT.
    """
    pub_pem = keypair.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signing_input = (
        _segment({"alg": "HS256", "kid": KID})
        + "."
        + _segment(
            {"iat": int(time.time()), "request_body_sha256": hashlib.sha256(BODY).hexdigest()}
        )
    )
    mac = hmac.new(pub_pem, signing_input.encode(), hashlib.sha256).digest()
    token = f"{signing_input}.{_b64url(mac)}"
    with pytest.raises(pw.WebhookVerificationError, match="Unexpected token algorithm"):
        pw.verify(token, BODY)


def test_stale_token_is_rejected(sign):
    """Plaid's tokens carry no `exp`, so the signature is valid forever.

    Without a freshness bound, any webhook ever captured can be replayed.
    """
    with pytest.raises(pw.WebhookVerificationError, match="old; limit is"):
        pw.verify(sign(iat=time.time() - pw.MAX_AGE_SECONDS - 60), BODY)


def test_token_without_body_digest_is_rejected(keypair):
    """Correctly signed, but nothing to bind it to a body."""
    token = jwt.encode({"iat": int(time.time())}, keypair, algorithm="ES256", headers={"kid": KID})
    with pytest.raises(pw.WebhookVerificationError, match="no body digest"):
        pw.verify(token, BODY)


def test_token_without_iat_is_rejected(keypair):
    token = jwt.encode(
        {"request_body_sha256": hashlib.sha256(BODY).hexdigest()},
        keypair,
        algorithm="ES256",
        headers={"kid": KID},
    )
    # Matches either layer: PyJWT's `require` rejecting it during decode, or the
    # explicit iat check after. Both mention the claim; a regression that drops
    # one is still caught by the other.
    with pytest.raises(pw.WebhookVerificationError, match="iat"):
        pw.verify(token, BODY)


# --------------------------------------------------------------------------- #
# Key retrieval
#
# Everything above stubs `_fetch_key` wholesale, which means the assertions
# about unknown and rotated-out keys are really assertions about the stub. The
# `expired_at` branch and the JWK field filtering live inside the real
# function, so they need the layer below stubbed instead: Plaid's client and
# Redis, rather than `_fetch_key` itself.
# --------------------------------------------------------------------------- #

class _FakeResponse:
    def __init__(self, key: dict):
        self._key = key

    def to_dict(self) -> dict:
        return {"key": self._key}


@pytest.fixture
def plaid_key(monkeypatch, jwk: dict):
    """Stub Plaid's key endpoint and bypass the cache."""

    served: dict = {"key": dict(jwk)}

    class _FakeRedis:
        def get(self, _key):
            return None  # always a miss, so the fetch path is the one under test

        def set(self, *_args, **_kwargs):
            pass

    class _FakeClient:
        def webhook_verification_key_get(self, _request):
            if served["key"] is None:
                raise plaid.ApiException(status=400)
            return _FakeResponse(served["key"])

    monkeypatch.setattr(pw, "redis_client", _FakeRedis())
    monkeypatch.setattr(pw, "get_client", lambda: _FakeClient())
    return served


def test_fetch_key_returns_jwk_members_only(plaid_key, jwk: dict):
    """Plaid returns bookkeeping fields that are not JWK members.

    `created_at`/`expired_at` are Plaid's own metadata. PyJWK has no reason to
    be handed them, and passing unknown members through is how a key ends up
    rejected for a reason that has nothing to do with the key.
    """
    plaid_key["key"] = {**jwk, "created_at": 1700000000, "expired_at": None}
    assert FETCH_KEY(KID) == jwk


def test_fetch_key_rejects_expired_key(plaid_key, jwk: dict):
    """The real rotation guard, not the stub's.

    Plaid keeps serving a retired key so webhooks signed just before rotation
    still validate, and marks it with `expired_at`. Honouring that is the
    difference between a rotation and a key that is trusted forever.
    """
    plaid_key["key"] = {**jwk, "expired_at": 1700000000}
    with pytest.raises(pw.WebhookVerificationError, match="expired"):
        FETCH_KEY(KID)


def test_fetch_key_treats_unknown_kid_as_verification_failure(plaid_key):
    """An unrecognised `kid` is the expected shape of a forgery.

    Plaid answers 400. Letting `ApiException` escape would surface a forged
    request as a 502 — an upstream fault we would go and investigate — rather
    than as the rejection it is.
    """
    plaid_key["key"] = None
    with pytest.raises(pw.WebhookVerificationError, match="did not recognise"):
        FETCH_KEY("who-dis")


def test_fetch_key_survives_redis_outage(monkeypatch, jwk: dict):
    """A cache outage must not become a verification outage.

    If Redis being unreachable could stop webhooks verifying, the pressure is
    to add a bypass — which is a far worse outcome than paying the round-trip.
    """

    class _BrokenRedis:
        def get(self, _key):
            raise redis.RedisError("down")

        def set(self, *_args, **_kwargs):
            raise redis.RedisError("down")

    class _FakeClient:
        def webhook_verification_key_get(self, _request):
            return _FakeResponse(dict(jwk))

    monkeypatch.setattr(pw, "redis_client", _BrokenRedis())
    monkeypatch.setattr(pw, "get_client", lambda: _FakeClient())
    assert FETCH_KEY(KID) == jwk
