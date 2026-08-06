from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.ledger import TransferDetectResult
from app.services.transfers import detect_transfers
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/transfers", tags=["transfers"])


@router.post("/detect", response_model=TransferDetectResult)
def run_transfer_detection(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: Session = Depends(get_db),
):
    """Pair internal movements across the user's own accounts.

    Idempotent: already-paired rows are skipped, so this is safe to re-run
    after every sync. With no dates it scans the trailing lookback window.
    """
    user = get_current_user(db)
    result = detect_transfers(db, user, start_date=start_date, end_date=end_date)
    return TransferDetectResult(**result.as_dict())
