from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.investments import AllocationOut, PortfolioOut
from app.services import investment
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/investments", tags=["investments"])


@router.get("/holdings", response_model=PortfolioOut)
def get_holdings(
    as_of: date | None = Query(
        None,
        description=(
            "Value the portfolio as it stood on this date. Each account falls "
            "back to its own most recent snapshot on or before it, so one "
            "stale-syncing account does not truncate the others."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Every position, with cost basis, unrealized gain and 1-day change."""
    user = get_current_user(db)
    return PortfolioOut.model_validate(investment.build_portfolio(db, user, as_of=as_of))


@router.get("/allocation", response_model=AllocationOut)
def get_allocation(
    as_of: date | None = Query(None, description="Allocation as of this date"),
    db: Session = Depends(get_db),
):
    """Asset-class mix plus the per-account drilldown, without the holdings rows.

    Derived from the same single pass as /holdings rather than recomputed, so
    the donut can never disagree with the table beneath it.
    """
    user = get_current_user(db)
    report = investment.build_portfolio(db, user, as_of=as_of)
    return AllocationOut(
        as_of_date=report.summary.as_of_date,
        total_value_cents=report.summary.total_value_cents,
        by_asset_class=report.by_asset_class,
        by_account=report.by_account,
    )
