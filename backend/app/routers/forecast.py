from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.forecast import ForecastOut
from app.services import forecasting
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/forecast", tags=["forecast"])


@router.get("", response_model=ForecastOut)
def get_forecast(
    horizon_days: int = Query(30, description="30, 60 or 90"),
    threshold_cents: int = Query(
        forecasting.DEFAULT_MIN_BALANCE_CENTS,
        ge=0,
        description="Liquidity floor; days below it are flagged as low-cash points",
    ),
    include_variable: bool = Query(
        True,
        description=(
            "Spread unspent budget across the remaining days. Off = committed "
            "cash flows only (recurring bills and income)."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Daily projected cash balance from today out to the horizon."""
    if horizon_days not in forecasting.ALLOWED_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon_days must be one of {list(forecasting.ALLOWED_HORIZONS)}",
        )

    user = get_current_user(db)
    forecast = forecasting.build_forecast(
        db,
        user,
        horizon_days=horizon_days,
        threshold_cents=threshold_cents,
        include_variable=include_variable,
    )
    return ForecastOut.model_validate(forecast)
