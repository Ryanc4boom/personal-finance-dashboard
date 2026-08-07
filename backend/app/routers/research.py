"""HTTP surface for the SEC research engine.

Endpoints are `def`, not `async def`, exactly as everywhere else in this app:
the work is blocking httpx calls and blocking Redis reads, so FastAPI runs them
on the threadpool where they belong instead of stalling the event loop.

A full report can take several seconds on a cold cache — it downloads a 10-K and
a proxy, each tens of megabytes, under a 6 req/s ceiling SEC enforces by IP ban.
That is why `sec_client` caches aggressively and why the errors below are mapped
to distinct status codes: a client that cannot tell "no such ticker" from "SEC
is rate-limiting you" will retry the wrong one.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.research import ResearchReportOut, TickerSearchOut
from app.services import research, sec_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.get("/search", response_model=TickerSearchOut)
def search(
    q: str = Query("", max_length=64, description="Ticker or company name fragment"),
    limit: int = Query(8, ge=1, le=25),
):
    """Typeahead over SEC's ticker index.

    Served from the same cached `company_tickers.json` the deep dive resolves
    against, so the search can never offer a ticker the report then rejects.
    """
    try:
        results = sec_client.search_tickers(q, limit=limit)
    except sec_client.SECError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TickerSearchOut(query=q, results=results)


@router.get("/{ticker}", response_model=ResearchReportOut)
def report(
    ticker: str = Path(..., max_length=16),
    price_cents: int | None = Query(
        None,
        ge=1,
        description=(
            "Value the stock at this price instead of the resolved one. Lets you "
            "ask what the P/E would be at a target entry price, and makes the "
            "valuation stage usable with no market-data key configured."
        ),
    ),
    db: Session = Depends(get_db),
):
    """The full four-stage scorecard for one ticker."""
    try:
        return ResearchReportOut.model_validate(
            research.analyze(db, ticker, price_cents)
        )
    except sec_client.TickerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except sec_client.FilingNotAvailable as exc:
        # The ticker is real and SEC answered; there is simply nothing to
        # analyse. 422 rather than 404 so the UI can show the explanation of
        # *why* — successor registrant, IFRS filer — instead of "not found".
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sec_client.SECError as exc:
        logger.warning("SEC request failed for %s: %s", ticker, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
