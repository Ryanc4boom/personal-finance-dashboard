import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict


class ForecastAccountOut(BaseModel):
    account_id: uuid.UUID
    name: str
    mask: str | None
    balance_cents: int


class ForecastEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    stream_id: uuid.UUID
    display_name: str
    # Signed, matching the ledger: positive is money in.
    amount_cents: int
    direction: str
    frequency: str
    is_subscription: bool


class ForecastDayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    opening_cents: int
    inflow_cents: int
    recurring_outflow_cents: int
    variable_outflow_cents: int
    closing_cents: int
    below_threshold: bool
    events: list[ForecastEventOut]


class LowCashPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    balance_cents: int
    days_away: int
    shortfall_cents: int


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_date: date
    end_date: date
    horizon_days: int
    starting_cash_cents: int
    ending_cash_cents: int
    # Balance projected for the last day of the current calendar month.
    month_end_cents: int
    min_balance_cents: int
    min_balance_date: date
    threshold_cents: int
    include_variable: bool

    total_inflow_cents: int
    total_recurring_outflow_cents: int
    total_variable_outflow_cents: int

    accounts: list[ForecastAccountOut]
    days: list[ForecastDayOut]
    # One entry per contiguous dip below the threshold, at its worst day.
    low_points: list[LowCashPointOut]
