"""Error reporting that is safe to point at a third party.

The default posture of every crash reporter is to send *everything* it can
reach: the request body, the query string, the headers, and the local variables
of each frame in the traceback. For an ordinary web app that is a reasonable
trade. Here, those four things are, respectively, a Plaid access token, an
account id, the `X-API-Key`, and an ORM object holding the user's balances.

So the integration is inverted. Nothing is forwarded unless it survives
`scrub_event`, which strips whole sections rather than trying to redact them
field by field — a deny-list would need updating every time a schema grows a
column, and the failure mode of forgetting is silent exfiltration.

What is deliberately kept: exception type, module, the stack *shape*, the HTTP
method and a templated path. That is enough to answer "which endpoint broke and
where", which is the entire point of telemetry.

Disabled unless `SENTRY_DSN` is set. `sentry-sdk` is not a hard dependency; if
the DSN is set and the package is missing this logs and carries on, because an
observability tool that can take the API down with it is worse than no
observability.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
# Applied to the free text that is kept — exception messages and log records.
# These are the strings most likely to have had a real value interpolated into
# them ("no category for AMZN Mktp US*2H48Z, -$104.99").
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Plaid tokens, in the three environments' formats.
    (re.compile(r"(?:access|link|public)-(?:sandbox|development|production)-[\w-]+"),
     "[token]"),
    # Fernet ciphertext: version byte 0x80 base64-encodes to a leading "gAAAAA".
    (re.compile(r"gAAAAA[A-Za-z0-9_=-]{20,}"), "[encrypted]"),
    # Currency amounts, with or without the symbol. Ordered before the bare-digit
    # rule so "$1,234.56" is replaced whole rather than leaving "$[num],[num]".
    (re.compile(r"-?\$\s?\d[\d,]*(?:\.\d+)?"), "[amount]"),
    (re.compile(r"\b-?\d[\d,]*\.\d{2}\b"), "[amount]"),
    # Any run of 4+ digits: account and routing numbers, card masks, ids.
    # Aggressive on purpose — it also eats HTTP status codes' neighbours and the
    # occasional port, and losing those is cheaper than leaking an account
    # number that happened to be in a message nobody predicted.
    (re.compile(r"\b\d{4,}\b"), "[num]"),
)


def redact(text: str) -> str:
    """Strip anything that could be a token, an amount or an account number."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


# Path segments that are values rather than route structure. Sent as-is they
# turn every request into its own unique "endpoint", which is both useless for
# grouping and a way of publishing account ids one error at a time.
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_NUMERIC = re.compile(r"^\d+$")


def template_path(path: str) -> str:
    """`/api/v1/transactions/9f3b…/notes` -> `/api/v1/transactions/{id}/notes`."""
    parts = []
    for segment in path.split("/"):
        if _UUID.match(segment) or _NUMERIC.match(segment):
            parts.append("{id}")
        else:
            parts.append(segment)
    return "/".join(parts)


# --------------------------------------------------------------------------- #
# Event scrubbing
# --------------------------------------------------------------------------- #
# Kept out of the request section. Everything else — headers, cookies, body,
# query string, environ — goes, rather than being filtered.
_REQUEST_KEEP = ("method",)


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any] | None:
    """Sentry `before_send`. Returns the event with everything sensitive removed.

    Written as a plain function over a dict so it can be tested without the SDK
    installed and without a network stub — see tests/test_telemetry.py.
    """
    # Machine name. On a personal deployment this is the owner's real name more
    # often than not.
    event.pop("server_name", None)
    # Populated from IP and auth when send_default_pii is on. Belt and braces.
    event.pop("user", None)
    # Free-form bag that callers attach objects to. There is no way to know what
    # is in it, so it does not travel.
    event.pop("extra", None)

    request = event.get("request")
    if isinstance(request, dict):
        kept = {k: request[k] for k in _REQUEST_KEEP if k in request}
        url = request.get("url")
        if isinstance(url, str):
            # Split on "?" first: the query string carries filter values —
            # dates, account ids, search terms typed by the user.
            kept["url"] = template_path(url.split("?", 1)[0])
        event["request"] = kept

    for entry in event.get("exception", {}).get("values", []):
        if isinstance(entry.get("value"), str):
            entry["value"] = redact(entry["value"])
        for frame in entry.get("stacktrace", {}).get("frames", []):
            # Locals are the worst offender: a frame inside the ingestion
            # pipeline holds the entire transaction row it was working on.
            frame.pop("vars", None)

    log = event.get("logentry")
    if isinstance(log, dict):
        if isinstance(log.get("message"), str):
            log["message"] = redact(log["message"])
        # `params` are the unformatted interpolation arguments — the actual
        # values, before they were rendered into the message.
        log.pop("params", None)

    scrubbed_crumbs = []
    for crumb in event.get("breadcrumbs", {}).get("values", []):
        if isinstance(crumb.get("message"), str):
            crumb["message"] = redact(crumb["message"])
        # A crumb's `data` is where the SQL statement and its bound parameters
        # end up for db spans, and the full URL for http ones.
        crumb.pop("data", None)
        scrubbed_crumbs.append(crumb)
    if "breadcrumbs" in event:
        event["breadcrumbs"]["values"] = scrubbed_crumbs

    return event


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

def init_telemetry() -> bool:
    """Start error reporting if a DSN is configured. True if it was enabled."""
    dsn = settings.sentry_dsn.strip()
    if not dsn:
        logger.info("SENTRY_DSN not set; error reporting is off")
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.error(
            "SENTRY_DSN is set but sentry-sdk is not installed. "
            "Run `pip install sentry-sdk` or unset SENTRY_DSN. Continuing without it."
        )
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # The two switches that matter. The first stops IP addresses, cookies
        # and request bodies being attached at all; the second stops frame
        # locals riding along in the traceback. scrub_event is the backstop for
        # both, not the only line.
        send_default_pii=False,
        include_local_variables=False,
        before_send=scrub_event,
        before_send_transaction=scrub_event,
        # Log records are forwarded by the logging integration by default. This
        # app logs merchant names and sync counts at INFO, so only genuine
        # errors are worth the exposure.
        before_breadcrumb=lambda crumb, hint: (
            None if crumb.get("category") == "query" else crumb
        ),
    )
    logger.info("Error reporting enabled (environment=%s)", settings.sentry_environment)
    return True
