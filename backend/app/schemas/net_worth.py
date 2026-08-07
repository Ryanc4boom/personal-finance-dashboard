import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict


class NetWorthPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    total_assets_cents: int
    # Positive magnitude. Net worth is assets minus this, never assets plus a
    # negative — the sign lives in the arithmetic, not the number.
    total_liabilities_cents: int
    net_worth_cents: int
    # ACTUAL when every account had a real balance that day, RECONSTRUCTED when
    # it was walked backwards through transaction history.
    source: str
    breakdown: dict


class NetWorthChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_cents: int
    end_cents: int
    delta_cents: int
    # Withheld when the starting net worth was zero or negative; a percentage
    # change off a negative base is meaningless, not just imprecise.
    delta_bps: int | None


class AccountBreakdownRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    name: str
    mask: str | None
    type: str
    subtype: str | None
    bucket: str
    balance_cents: int
    is_liability: bool


class NetWorthHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    range_key: str
    start_date: date | None
    end_date: date | None

    current_assets_cents: int
    current_liabilities_cents: int
    current_net_worth_cents: int

    change: NetWorthChangeOut | None
    points: list[NetWorthPointOut]
    accounts: list[AccountBreakdownRowOut]
    breakdown: dict


class BackfillResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_date: str | None
    end_date: str | None
    days_computed: int
    snapshots_created: int
    snapshots_updated: int
    accounts_considered: int
    warnings: list[str]
