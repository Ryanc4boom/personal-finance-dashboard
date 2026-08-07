"""Transport layer for SEC EDGAR. Everything that touches sec.gov goes here.

**Why SEC is the primary source rather than a vendor fundamentals API.** The
whole point of the Phase 5 framework is that the numbers are auditable back to a
filing. A vendor API hands you `revenue: 383285000000` with no way to see how it
was assembled; EDGAR hands you the XBRL fact the company itself tagged, with the
accession number that produced it. Every figure this engine reports can be
traced to a document URL, and the UI shows that URL. A vendor number cannot be
checked; a filing number can.

**Three rules SEC enforces that will get you banned if you ignore them:**

1. A `User-Agent` declaring the application and a contact address. Without it
   EDGAR answers **403, not 429** — which reads like a bug in your code rather
   than a policy rejection, and costs an hour before you look at the header.
2. Ten requests per second, enforced by IP block rather than a soft throttle.
   `_RateLimiter` below is process-wide and deliberately set under the ceiling.
3. `data.sec.gov` (JSON APIs) and `www.sec.gov` (document archives) are separate
   hosts with separate paths but share the same rate budget, so one limiter
   guards both.

**Caching is not an optimisation here, it is politeness.** An accepted filing is
immutable — its bytes will never change — so documents cache for a week. The
company-facts blob does change (daily, as new filings are accepted) but is
enormous, routinely 10-20MB of JSON for a large filer. It is stored zlib
compressed under a bytes-mode Redis client, because the shared `decode_responses=True`
client in core.redis_client would force every read of a 15MB payload through a
UTF-8 decode to hand us a string we immediately parse as JSON anyway.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

DATA_HOST = "https://data.sec.gov"
WWW_HOST = "https://www.sec.gov"

# Ticker -> CIK. One ~1MB file covering every exchange-listed filer. There is no
# per-ticker lookup endpoint; this file *is* the index.
TICKER_MAP_URL = f"{WWW_HOST}/files/company_tickers.json"
TICKER_MAP_CACHE_SECONDS = 86_400

_CACHE_PREFIX = "sec:v1"

# Separate from core.redis_client.client on purpose: that one decodes responses
# to str, which is wrong for the compressed blobs stored here.
_blob_redis = redis.Redis.from_url(settings.redis_url, decode_responses=False)


class SECError(RuntimeError):
    """A SEC request failed in a way the caller should surface, not retry."""


class TickerNotFound(SECError):
    def __init__(self, ticker: str):
        super().__init__(f"No SEC filer found for ticker {ticker!r}")
        self.ticker = ticker


class FilingNotAvailable(SECError):
    """The filer exists but has not filed the form we need."""


@dataclass(frozen=True)
class Filing:
    """One accepted filing, from the submissions index."""

    form: str
    accession_number: str
    filing_date: date
    report_date: date | None
    primary_document: str
    cik: int

    @property
    def document_url(self) -> str:
        """Direct URL to the primary document.

        The archive path uses the CIK with leading zeros *stripped* and the
        accession number with dashes *removed* — the one place in EDGAR where
        the padded form is wrong. Getting this backwards yields a 404 that looks
        like a missing filing.
        """
        accn = self.accession_number.replace("-", "")
        return f"{WWW_HOST}/Archives/edgar/data/{self.cik}/{accn}/{self.primary_document}"

    @property
    def filing_index_url(self) -> str:
        """Human-readable index page — what we link a user to for verification."""
        accn = self.accession_number.replace("-", "")
        return (
            f"{WWW_HOST}/Archives/edgar/data/{self.cik}/{accn}/"
            f"{self.accession_number}-index.htm"
        )


class _RateLimiter:
    """Process-wide minimum spacing between SEC requests.

    A token bucket would allow a burst, and a burst is exactly what triggers the
    block. Flat spacing is the conservative read of the policy and costs nothing
    here, since a research request makes a handful of calls, not thousands.
    """

    def __init__(self, per_second: float):
        self._min_interval = 1.0 / max(per_second, 0.1)
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


_limiter = _RateLimiter(settings.sec_rate_limit_per_second)

# Long read timeout because a primary 10-K document is genuinely large; short
# connect timeout because a hung connect should fail fast rather than burn the
# request's whole budget.
_timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
_client = httpx.Client(
    timeout=_timeout,
    follow_redirects=True,
    headers={
        "User-Agent": settings.sec_contact,
        "Accept-Encoding": "gzip, deflate",
    },
)


def _cache_get(key: str) -> bytes | None:
    try:
        return _blob_redis.get(f"{_CACHE_PREFIX}:{key}")
    except redis.RedisError:
        # A cold cache is slower, not wrong. Never let Redis being down take the
        # research engine with it.
        logger.warning("SEC cache read failed for %s; falling through to SEC", key)
        return None


def _cache_set(key: str, payload: bytes, ttl: int) -> None:
    try:
        _blob_redis.set(f"{_CACHE_PREFIX}:{key}", payload, ex=ttl)
    except redis.RedisError:
        logger.warning("SEC cache write failed for %s", key)


def _fetch(url: str, *, cache_key: str, ttl: int, max_bytes: int | None = None) -> bytes:
    """GET with rate limiting and a compressed Redis cache."""
    cached = _cache_get(cache_key)
    if cached is not None:
        return zlib.decompress(cached)

    _limiter.acquire()
    try:
        response = _client.get(url)
    except httpx.HTTPError as exc:
        raise SECError(f"Could not reach SEC ({url}): {exc}") from exc

    if response.status_code == 404:
        raise FilingNotAvailable(f"SEC has no document at {url}")
    if response.status_code == 403:
        raise SECError(
            "SEC rejected the request (403). This is almost always the "
            "User-Agent: set SEC_USER_AGENT to something identifying you, e.g. "
            "'Your Name (you@example.com)'."
        )
    if response.status_code == 429:
        raise SECError("SEC rate limit hit (429). Lower SEC_RATE_LIMIT_PER_SECOND.")
    if response.status_code >= 400:
        raise SECError(f"SEC returned {response.status_code} for {url}")

    body = response.content
    if max_bytes is not None and len(body) > max_bytes:
        logger.info("Truncating %s from %d to %d bytes", url, len(body), max_bytes)
        body = body[:max_bytes]

    _cache_set(cache_key, zlib.compress(body, 1), ttl)
    return body


def _fetch_json(url: str, *, cache_key: str, ttl: int) -> Any:
    raw = _fetch(url, cache_key=cache_key, ttl=ttl)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SECError(f"SEC returned malformed JSON for {url}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Ticker -> CIK
# --------------------------------------------------------------------------- #


def _ticker_map() -> dict[str, tuple[int, str]]:
    """`{TICKER: (cik, company_name)}` for every exchange-listed filer.

    The file is a dict keyed by *row index*, not by ticker — `{"0": {...}}` —
    which is a serialised dataframe, not a lookup table. We invert it.
    """
    payload = _fetch_json(
        TICKER_MAP_URL, cache_key="tickers", ttl=TICKER_MAP_CACHE_SECONDS
    )
    mapping: dict[str, tuple[int, str]] = {}
    for row in payload.values():
        ticker = str(row.get("ticker", "")).strip().upper()
        if ticker:
            mapping[ticker] = (int(row["cik_str"]), str(row.get("title", ticker)))
    return mapping


def resolve_ticker(ticker: str) -> tuple[int, str]:
    """`(cik, company_name)` for a ticker, or raise `TickerNotFound`.

    Class shares are listed with a dot in EDGAR (`BRK.B`) but typed by users with
    a dash (`BRK-B`) as often as not, so both are tried.
    """
    symbol = ticker.strip().upper()
    if not symbol:
        raise TickerNotFound(ticker)

    mapping = _ticker_map()
    for candidate in (symbol, symbol.replace("-", "."), symbol.replace(".", "-")):
        if candidate in mapping:
            return mapping[candidate]
    raise TickerNotFound(ticker)


def search_tickers(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Typeahead over the ticker index.

    Exact ticker first, then ticker prefix, then company-name substring — so
    typing "A" offers A/AAPL/AMZN rather than an alphabetical accident.
    """
    q = query.strip().upper()
    if not q:
        return []

    mapping = _ticker_map()
    exact: list[tuple[str, int, str]] = []
    prefix: list[tuple[str, int, str]] = []
    name_hit: list[tuple[str, int, str]] = []

    for ticker, (cik, name) in mapping.items():
        if ticker == q:
            exact.append((ticker, cik, name))
        elif ticker.startswith(q):
            prefix.append((ticker, cik, name))
        elif q in name.upper():
            name_hit.append((ticker, cik, name))

    prefix.sort(key=lambda row: (len(row[0]), row[0]))
    name_hit.sort(key=lambda row: (len(row[2]), row[0]))

    return [
        {"ticker": ticker, "cik": cik, "name": name}
        for ticker, cik, name in (exact + prefix + name_hit)[:limit]
    ]


# --------------------------------------------------------------------------- #
# Filer data
# --------------------------------------------------------------------------- #


def company_facts(cik: int) -> dict[str, Any]:
    """Every XBRL fact the filer has ever tagged, across every filing.

    This single call is the source for framework stages 2-4. It is large; see
    the module docstring on why it is cached compressed.
    """
    padded = f"CIK{cik:010d}"
    return _fetch_json(
        f"{DATA_HOST}/api/xbrl/companyfacts/{padded}.json",
        cache_key=f"facts:{cik}",
        ttl=settings.sec_facts_cache_seconds,
    )


def submissions(cik: int) -> dict[str, Any]:
    """The filer's metadata and recent filing index."""
    padded = f"CIK{cik:010d}"
    return _fetch_json(
        f"{DATA_HOST}/submissions/{padded}.json",
        cache_key=f"subs:{cik}",
        ttl=settings.sec_facts_cache_seconds,
    )


def industry(cik: int) -> tuple[str, str]:
    """The filer's SIC code and its description.

    Needed because several of the framework's ratios assume a non-financial
    issuer. A bank's interest expense is what it pays depositors — a cost of
    revenue, not debt service — so scoring its interest coverage on the same
    scale as a manufacturer's produces a confident wrong answer.
    """
    subs = submissions(cik)
    return str(subs.get("sic") or ""), str(subs.get("sicDescription") or "")


def latest_filing(cik: int, forms: tuple[str, ...]) -> Filing | None:
    """Most recent filing matching any of `forms`, newest first.

    `filings.recent` is column-oriented — parallel arrays, not a list of
    objects — so the fields are zipped back together here.

    Amendments are matched too (`10-K/A` for `10-K`) because an amended annual
    report supersedes the original, and the framework should read the corrected
    document. Ordering is by filing date, so the amendment naturally wins.
    """
    payload = submissions(cik)
    recent = payload.get("filings", {}).get("recent", {})

    form_list = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    documents = recent.get("primaryDocument", [])

    wanted = {f.upper() for f in forms}
    best: Filing | None = None

    for index, form in enumerate(form_list):
        base = form.upper().split("/")[0]
        if form.upper() not in wanted and base not in wanted:
            continue
        if index >= len(accessions) or index >= len(filing_dates):
            continue
        primary = documents[index] if index < len(documents) else ""
        if not primary:
            # No primary document means an exhibit-only or paper filing; the
            # text parser has nothing to read.
            continue

        report_raw = report_dates[index] if index < len(report_dates) else ""
        candidate = Filing(
            form=form,
            accession_number=accessions[index],
            filing_date=date.fromisoformat(filing_dates[index]),
            report_date=date.fromisoformat(report_raw) if report_raw else None,
            primary_document=primary,
            cik=cik,
        )
        if best is None or candidate.filing_date > best.filing_date:
            best = candidate

    return best


def filing_document(filing: Filing) -> str:
    """The primary document as text.

    Decoded permissively: EDGAR documents are nominally UTF-8 but decades of
    filer software have put stray Windows-1252 bytes in them, and one bad byte
    must not lose the whole filing.
    """
    raw = _fetch(
        filing.document_url,
        cache_key=f"doc:{filing.accession_number}:{filing.primary_document}",
        ttl=settings.sec_document_cache_seconds,
        max_bytes=settings.sec_max_document_bytes,
    )
    return raw.decode("utf-8", errors="replace")
