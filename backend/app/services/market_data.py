"""Share price resolution for the valuation stage.

SEC publishes no prices, and P/E and PEG are meaningless without one. Three
sources are tried in descending order of trustworthiness:

1. **An explicit override** on the request. Lets the user ask "what would the
   P/E be at $150" without touching config, and makes the valuation stage
   testable without a network call.
2. **The `security` table.** Phase 4 already stores a close price and its date
   for every instrument in the user's portfolio. If they hold the ticker, the
   price is already here, it came from their own custodian, and fetching it
   again from a third party would be strictly worse.
3. **Finnhub**, if a key is configured.

If all three miss, the result is `None` and the valuation checks report
`UNKNOWN`. That is the entire point of this module's design: a research tool
that invents a price to avoid an empty cell produces a P/E that looks exactly
like a real one. An honest gap is recoverable; a fabricated ratio is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import httpx
import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis_client import client as redis_client
from app.models import Security

logger = logging.getLogger(__name__)

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"

# Source labels, surfaced to the UI so a price is never anonymous.
SOURCE_OVERRIDE = "OVERRIDE"
SOURCE_PORTFOLIO = "PORTFOLIO"
SOURCE_FINNHUB = "FINNHUB"


@dataclass(frozen=True)
class PriceQuote:
    price_cents: int
    source: str
    as_of: date | None
    # True when the price is a stored close rather than a live quote, so the UI
    # can date the valuation instead of implying it is current.
    is_stale_close: bool


def _to_cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _from_portfolio(db: Session, ticker: str) -> PriceQuote | None:
    """The close price Phase 4 already stores for a held security."""
    security = db.scalar(
        select(Security)
        .where(
            Security.ticker_symbol == ticker.upper(),
            Security.close_price_cents.is_not(None),
        )
        .order_by(Security.close_price_as_of.desc().nullslast())
        .limit(1)
    )
    if security is None or security.close_price_cents is None:
        return None
    return PriceQuote(
        price_cents=security.close_price_cents,
        source=SOURCE_PORTFOLIO,
        as_of=security.close_price_as_of,
        is_stale_close=True,
    )


def _from_finnhub(ticker: str) -> PriceQuote | None:
    if not settings.finnhub_api_key:
        return None

    cache_key = f"quote:finnhub:{ticker.upper()}"
    try:
        cached = redis_client.get(cache_key)
    except redis.RedisError:
        cached = None
    if cached:
        return PriceQuote(int(cached), SOURCE_FINNHUB, date.today(), False)

    try:
        response = httpx.get(
            FINNHUB_QUOTE_URL,
            params={"symbol": ticker.upper(), "token": settings.finnhub_api_key},
            timeout=httpx.Timeout(10.0),
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # A quote provider being down degrades the valuation stage to UNKNOWN.
        # It must not fail the other three stages, which need no price at all.
        logger.warning("Finnhub quote failed for %s: %s", ticker, exc)
        return None

    current = payload.get("c")
    # Finnhub answers 200 with `c: 0` for an unknown symbol rather than a 404.
    if not current:
        return None

    cents = _to_cents(Decimal(str(current)))
    try:
        redis_client.set(cache_key, cents, ex=settings.market_price_cache_seconds)
    except redis.RedisError:
        pass
    return PriceQuote(cents, SOURCE_FINNHUB, date.today(), False)


def resolve_price(
    db: Session, ticker: str, override_cents: int | None = None
) -> PriceQuote | None:
    """Best available share price, or None. See the module docstring for order."""
    if override_cents is not None and override_cents > 0:
        return PriceQuote(override_cents, SOURCE_OVERRIDE, date.today(), False)

    return _from_portfolio(db, ticker) or _from_finnhub(ticker)
