import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Category, RecurringStream
from app.models.enums import StatusSource, StreamStatus
from app.schemas.recurring import (
    DetectionResultOut,
    ForgottenStreamOut,
    PriceChangeOut,
    RecurringStreamOut,
    RecurringStreamUpdate,
    RenewalOut,
    SubscriptionMetricsOut,
    UpcomingRenewalsOut,
)
from app.services import recurring as recurring_service
from app.services import subscriptions as subscription_service
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/recurring", tags=["recurring"])


def _to_out(stream: RecurringStream, today: date) -> RecurringStreamOut:
    """Flatten the joined category and attach the derived per-period figures."""
    out = RecurringStreamOut.model_validate(stream)
    out.category_name = stream.category.name if stream.category else None
    out.monthly_cents = subscription_service.monthly_cents(
        stream.expected_amount_cents, stream.frequency
    )
    out.annual_cents = subscription_service.annualized_cents(
        stream.expected_amount_cents, stream.frequency
    )
    out.days_until_next = (stream.next_expected_date - today).days
    return out


@router.get("", response_model=list[RecurringStreamOut])
def list_streams(
    status: str | None = Query(None, description="ACTIVE | PAUSED | CANCELLED"),
    is_subscription: bool | None = Query(None),
    direction: str | None = Query(None, description="INFLOW | OUTFLOW"),
    db: Session = Depends(get_db),
):
    """Detected recurring streams, soonest renewal first."""
    user = get_current_user(db)
    stmt = select(RecurringStream).where(RecurringStream.user_id == user.id)

    if status is not None:
        stmt = stmt.where(RecurringStream.status == status)
    if is_subscription is not None:
        stmt = stmt.where(RecurringStream.is_subscription.is_(is_subscription))
    if direction is not None:
        stmt = stmt.where(RecurringStream.direction == direction)

    # Cancelled streams sink to the bottom: they are history, not commitments,
    # but they stay visible so the user can see what they turned off.
    streams = db.scalars(
        stmt.order_by(
            (RecurringStream.status == StreamStatus.CANCELLED.value).asc(),
            RecurringStream.next_expected_date.asc(),
            RecurringStream.display_name.asc(),
        )
    ).all()

    today = date.today()
    return [_to_out(s, today) for s in streams]


@router.post("/detect", response_model=DetectionResultOut)
def run_detection(
    lookback_days: int = Query(
        recurring_service.DEFAULT_LOOKBACK_DAYS, ge=60, le=1825
    ),
    db: Session = Depends(get_db),
):
    """Re-fit every stream from transaction history.

    Idempotent: streams are upserted on their identity, and a status the user set
    by hand is left alone. Safe to run after every sync.
    """
    user = get_current_user(db)
    today = date.today()
    result = recurring_service.detect_recurring(
        db,
        user,
        start_date=today - timedelta(days=lookback_days),
        end_date=today,
        today=today,
    )
    return DetectionResultOut(**result.as_dict())


@router.get("/metrics", response_model=SubscriptionMetricsOut)
def get_metrics(db: Session = Depends(get_db)):
    """Recurring commitment totals, price hikes and forgotten-subscription flags."""
    user = get_current_user(db)
    metrics = subscription_service.subscription_metrics(db, user)
    return SubscriptionMetricsOut(
        recurring_monthly_cents=metrics.recurring_monthly_cents,
        recurring_annual_cents=metrics.recurring_annual_cents,
        subscription_monthly_cents=metrics.subscription_monthly_cents,
        subscription_annual_cents=metrics.subscription_annual_cents,
        recurring_income_monthly_cents=metrics.recurring_income_monthly_cents,
        active_subscription_count=metrics.active_subscription_count,
        active_recurring_count=metrics.active_recurring_count,
        paused_count=metrics.paused_count,
        cancelled_count=metrics.cancelled_count,
        price_hikes=[PriceChangeOut.model_validate(h) for h in metrics.price_hikes],
        forgotten=[ForgottenStreamOut.model_validate(f) for f in metrics.forgotten],
    )


@router.get("/upcoming", response_model=UpcomingRenewalsOut)
def get_upcoming(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Every expected charge in the next `days`, chronologically.

    A weekly stream appears once per occurrence, not once per stream — this is
    "what will hit my account", not "what am I subscribed to".
    """
    user = get_current_user(db)
    today = date.today()
    renewals = subscription_service.upcoming_renewals(db, user, days=days, today=today)
    return UpcomingRenewalsOut(
        start_date=today,
        end_date=today + timedelta(days=days),
        total_cents=sum(
            r.amount_cents for r in renewals if r.direction == "OUTFLOW"
        ),
        renewals=[RenewalOut.model_validate(r) for r in renewals],
    )


@router.patch("/{stream_id}", response_model=RecurringStreamOut)
def update_stream(
    stream_id: uuid.UUID,
    payload: RecurringStreamUpdate,
    db: Session = Depends(get_db),
):
    """Edit one stream.

    Changing `status` or `is_subscription` marks that field as user-owned, which
    makes it permanently immune to re-detection — the same guarantee the ledger's
    inline category picker gives.
    """
    user = get_current_user(db)
    stream = db.scalar(
        select(RecurringStream).where(
            RecurringStream.id == stream_id, RecurringStream.user_id == user.id
        )
    )
    if stream is None:
        raise HTTPException(status_code=404, detail="Recurring stream not found")

    fields = payload.model_dump(exclude_unset=True)

    if "status" in fields and fields["status"] is not None:
        stream.status = fields["status"]
        stream.status_source = StatusSource.USER.value

    if "is_subscription" in fields and fields["is_subscription"] is not None:
        stream.is_subscription = fields["is_subscription"]
        stream.is_subscription_locked = True

    if "category_id" in fields:
        category_id = fields["category_id"]
        if category_id is not None:
            category = db.scalar(
                select(Category).where(
                    Category.id == category_id,
                    or_(Category.user_id.is_(None), Category.user_id == user.id),
                )
            )
            if category is None:
                raise HTTPException(status_code=404, detail="Category not found")
        stream.category_id = category_id

    if fields.get("expected_amount_cents") is not None:
        stream.expected_amount_cents = fields["expected_amount_cents"]

    db.commit()
    db.refresh(stream)
    return _to_out(stream, date.today())


@router.delete("/{stream_id}", status_code=204)
def delete_stream(stream_id: uuid.UUID, db: Session = Depends(get_db)):
    """Forget a stream entirely.

    Prefer PATCHing the status to CANCELLED: deleting only removes the inference,
    and the next detection pass will happily recreate it from the same history.
    """
    user = get_current_user(db)
    stream = db.scalar(
        select(RecurringStream).where(
            RecurringStream.id == stream_id, RecurringStream.user_id == user.id
        )
    )
    if stream is None:
        raise HTTPException(status_code=404, detail="Recurring stream not found")
    db.delete(stream)
    db.commit()
