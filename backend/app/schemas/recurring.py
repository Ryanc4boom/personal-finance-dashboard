import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import RecurrenceFrequency, StreamStatus


class RecurringStreamOut(BaseModel):
    """One detected stream. Every money field is integer cents, always positive;
    `direction` carries the sign."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    merchant_id: uuid.UUID | None
    category_id: uuid.UUID | None
    normalized_key: str
    display_name: str
    frequency: str
    direction: str

    expected_amount_cents: int
    last_amount_cents: int
    # Median deviation from the baseline, in basis points. 0 = fixed price.
    amount_variance_bps: int

    first_date: date
    last_date: date
    next_expected_date: date
    median_interval_days: int
    occurrence_count: int

    status: str
    status_source: str
    is_subscription: bool
    is_subscription_locked: bool

    category_name: str | None = None
    # Derived, not stored — see services/subscriptions.
    monthly_cents: int = 0
    annual_cents: int = 0
    days_until_next: int = 0


class RecurringStreamUpdate(BaseModel):
    """User-owned fields. Setting `status` or `is_subscription` locks that field
    against the next detection pass."""

    status: str | None = None
    is_subscription: bool | None = None
    category_id: uuid.UUID | None = None
    expected_amount_cents: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def check_enums(self) -> "RecurringStreamUpdate":
        # Mirrors the DB's allowed values so a bad payload is a 422, not a 500
        # from a constraint violation three layers down.
        if self.status is not None and self.status not in {s.value for s in StreamStatus}:
            raise ValueError(f"status must be one of {[s.value for s in StreamStatus]}")
        return self


class DetectionResultOut(BaseModel):
    scanned: int
    groups_considered: int
    streams_created: int
    streams_updated: int
    streams_unchanged: int
    transactions_linked: int
    stale_marked: int
    warnings: list[str]


class PriceChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stream_id: uuid.UUID
    display_name: str
    baseline_cents: int
    current_cents: int
    delta_cents: int
    delta_bps: int
    # What the increase costs over a year — the number worth acting on.
    annual_impact_cents: int
    last_date: date


class ForgottenStreamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stream_id: uuid.UUID
    display_name: str
    expected_amount_cents: int
    frequency: str
    last_date: date
    next_expected_date: date
    days_overdue: int
    status: str


class SubscriptionMetricsOut(BaseModel):
    # All recurring outflows, rent and utilities included.
    recurring_monthly_cents: int
    recurring_annual_cents: int
    # The cancellable subset.
    subscription_monthly_cents: int
    subscription_annual_cents: int
    recurring_income_monthly_cents: int

    active_subscription_count: int
    active_recurring_count: int
    paused_count: int
    cancelled_count: int

    price_hikes: list[PriceChangeOut]
    forgotten: list[ForgottenStreamOut]


class RenewalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    stream_id: uuid.UUID
    display_name: str
    amount_cents: int
    frequency: str
    direction: str
    is_subscription: bool
    category_name: str | None
    days_away: int


class UpcomingRenewalsOut(BaseModel):
    start_date: date
    end_date: date
    total_cents: int
    renewals: list[RenewalOut]


FREQUENCIES = [f.value for f in RecurrenceFrequency]
