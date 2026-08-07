from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.net_worth import (
    AccountBreakdownRowOut,
    BackfillResultOut,
    NetWorthHistoryOut,
)
from app.services import net_worth
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/net-worth", tags=["net-worth"])


@router.get("/history", response_model=NetWorthHistoryOut)
def get_history(
    range: str = Query("1Y", description="1M, 3M, 6M, 1Y or ALL"),
    db: Session = Depends(get_db),
):
    """Daily assets, liabilities and net worth over the window.

    Stored snapshots are read where they exist; today is always recomputed live
    so the headline number matches the account balances on the dashboard rather
    than whatever the last backfill happened to persist.
    """
    range_key = range.upper()
    if range_key not in net_worth.RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"range must be one of {list(net_worth.RANGE_DAYS)}",
        )

    user = get_current_user(db)
    return NetWorthHistoryOut.model_validate(
        net_worth.history(db, user, range_key=range_key)
    )


@router.get("/accounts", response_model=list[AccountBreakdownRowOut])
def get_accounts(db: Session = Depends(get_db)):
    """Asset vs. liability breakdown, one row per account."""
    user = get_current_user(db)
    return [
        AccountBreakdownRowOut.model_validate(row)
        for row in net_worth.account_breakdown(db, user)
    ]


@router.post("/backfill", response_model=BackfillResultOut)
def run_backfill(
    start: date | None = Query(None, description="Defaults to earliest activity"),
    end: date | None = Query(None, description="Defaults to today"),
    db: Session = Depends(get_db),
):
    """Reconstruct and persist historical snapshots.

    Explicitly triggered, never a side effect of reading /history: writing
    thousands of rows from inside a GET would make a dashboard refresh a
    write-heavy operation and would hide the cost from the user. Idempotent —
    re-running over the same window updates in place.
    """
    user = get_current_user(db)
    return BackfillResultOut.model_validate(
        net_worth.backfill(db, user, start=start, end=end)
    )
