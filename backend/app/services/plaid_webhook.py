"""Verification of the JWT Plaid signs each webhook with.

The webhook endpoint is the only route in this application that a third party
is *supposed* to call, which means it is also the only one that cannot be
protected by asking who the caller is. Anyone on the internet can POST to it.
Without a signature check the handler will act on whatever arrives: forcing
syncs against a real Plaid Item (billed per call, and rate-limited by Plaid
rather than by us) or flipping an Item to `error`, which disables a user's
linked bank account until they re-link it. Guessing an `item_id` is the entire
attack.

Plaid signs the body with ES256 and puts the token in `Plaid-Verification`. The
token's payload carries `request_body_sha256` rather than the body itself, so
verification is two independent steps:

1. The token is genuinely Plaid's — signature verifies against the public key
   Plaid publishes for the `kid` named in the token header.
2. The body is the one that token was minted for — SHA-256 of the *raw* bytes
   equals the digest inside the verified payload.

Both are required. Step 1 alone lets an attacker replay a valid token against a
substituted body; step 2 alone is a checksum anyone can compute.

There is deliberately no bypass flag. A `PLAID_SKIP_WEBHOOK_VERIFY` escape
hatch is the kind of setting that exists for one afternoon of local debugging
and is still enabled in production two years later, and the failure is silent —
everything works exactly as well with verification off. Local development never
needs one: without a public `PLAID_WEBHOOK_URL` Plaid has nowhere to send a
webhook, so the endpoint is never exercised anyway.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import jwt
import plaid
import redis
from jwt import PyJWK
from plaid.model.webhook_verification_key_get_request import (
    WebhookVerificationKeyGetRequest,
)

from app.core.redis_client import client as redis_client
from app.services.plaid_client import get_client

logger = logging.getLogger(__name__)

VERIFICATION_HEADER = "Plaid-Verification"

# ES256 and nothing else. PyJWT selects the verification algorithm from the
# token's own header unless told otherwise, which is the classic JWT
# vulnerability in both its forms: `alg: none` skips verification entirely, and
# `alg: HS256` makes the library treat the *public* EC key as an HMAC secret —
# and that key is public, so an attacker can mint tokens with it. Pinning the
# algorithm list closes both without needing to reason about either.
ALGORITHM = "ES256"

# Plaid's own guidance is to reject webhooks whose token was issued more than
# five minutes ago. The signature stays valid forever otherwise, so without this
# a token captured from any past webhook can be replayed indefinitely against
# its original body. Five minutes bounds that to a window an attacker has to be
# actively watching to hit.
MAX_AGE_SECONDS = 300

_KEY_CACHE_PREFIX = "plaid:webhook:jwk"
# Long, because these rotate rarely and a cache miss costs a synchronous Plaid
# round-trip inside a request Plaid is waiting on. Rotation is still handled:
# a new `kid` is simply a key this cache has never seen.
_KEY_CACHE_TTL = 24 * 60 * 60


class WebhookVerificationError(Exception):
    """The request did not come from Plaid, or did not arrive intact."""


def _fetch_key(key_id: str) -> dict:
    """The JWK for `key_id`, from cache or from Plaid.

    Cached by key ID rather than fetched per request: Plaid waits on this
    endpoint, and a network call to Plaid in order to validate a call from
    Plaid is a lot of latency to spend on every webhook.
    """
    cache_key = f"{_KEY_CACHE_PREFIX}:{key_id}"
    try:
        cached = redis_client.get(cache_key)
    except redis.RedisError:
        # Redis being down must not stop verification — that would turn a cache
        # outage into either an outage of webhook ingestion or, far worse, a
        # reason for someone to add a bypass. Just pay the round-trip.
        logger.warning("Webhook key cache read failed for %s", key_id)
        cached = None

    if cached:
        return json.loads(cached)

    try:
        response = get_client().webhook_verification_key_get(
            WebhookVerificationKeyGetRequest(key_id=key_id)
        )
    except plaid.ApiException as exc:
        # An unrecognised `kid` lands here as a 400. That is the expected shape
        # of a forged token, so it is a verification failure and not a 502.
        raise WebhookVerificationError(
            f"Plaid did not recognise webhook key {key_id!r}"
        ) from exc

    key = response.to_dict().get("key") or {}

    # A rotated-out key must not verify anything. Plaid keeps returning it so
    # that in-flight webhooks signed just before rotation still validate, and
    # marks it with `expired_at`; honouring that is the difference between a
    # rotation and a key that is trusted forever.
    if key.get("expired_at"):
        raise WebhookVerificationError(f"Webhook key {key_id!r} is expired")

    # Only the fields that define the EC public key. Plaid also returns
    # `created_at`/`expired_at`, which are not JWK members and which PyJWK has
    # no reason to be handed.
    jwk = {k: key[k] for k in ("kty", "crv", "x", "y", "kid", "alg", "use") if k in key}

    try:
        redis_client.set(cache_key, json.dumps(jwk), ex=_KEY_CACHE_TTL)
    except redis.RedisError:
        pass
    return jwk


def verify(header_value: str | None, raw_body: bytes) -> dict:
    """Verify a webhook, returning the decoded token payload.

    `raw_body` must be the exact bytes off the wire. Re-serialising the parsed
    JSON produces a different digest for reasons that have nothing to do with
    authenticity — key order, unicode escaping, whitespace — and would reject
    every genuine webhook.

    Raises `WebhookVerificationError` on anything short of a full pass.
    """
    if not header_value:
        raise WebhookVerificationError(f"Missing {VERIFICATION_HEADER} header")

    try:
        header = jwt.get_unverified_header(header_value)
    except jwt.PyJWTError as exc:
        raise WebhookVerificationError(f"Malformed verification token: {exc}") from exc

    # Read *only* the kid from the unverified header, and check the algorithm
    # claim before spending a lookup on the key it names.
    if header.get("alg") != ALGORITHM:
        raise WebhookVerificationError(
            f"Unexpected token algorithm {header.get('alg')!r}; expected {ALGORITHM}"
        )
    key_id = header.get("kid")
    if not key_id:
        raise WebhookVerificationError("Verification token has no key ID")

    jwk = _fetch_key(key_id)

    try:
        payload = jwt.decode(
            header_value,
            key=PyJWK(jwk, algorithm=ALGORITHM).key,
            algorithms=[ALGORITHM],
            # Plaid's tokens carry `iat` but no `exp`, so there is no expiry to
            # verify; freshness is enforced against `iat` below instead.
            options={"verify_exp": False, "require": ["iat"]},
        )
    except jwt.PyJWTError as exc:
        raise WebhookVerificationError(f"Signature verification failed: {exc}") from exc

    issued_at = payload.get("iat")
    if not isinstance(issued_at, (int, float)):
        raise WebhookVerificationError("Verification token has no usable iat")
    age = time.time() - issued_at
    if age > MAX_AGE_SECONDS:
        raise WebhookVerificationError(
            f"Verification token is {int(age)}s old; limit is {MAX_AGE_SECONDS}s"
        )

    expected = payload.get("request_body_sha256")
    if not expected:
        raise WebhookVerificationError("Verification token has no body digest")

    actual = hashlib.sha256(raw_body).hexdigest()
    # compare_digest rather than `==`. The digest is public and this is a very
    # hard timing target, but a constant-time compare of two hex strings costs
    # nothing and removes the need to have that argument.
    if not hmac.compare_digest(actual, expected):
        raise WebhookVerificationError("Request body does not match signed digest")

    return payload
