"""Wire format for the portfolio analytics engine.

`quantity` is serialised as a **string**, not a float. Share counts routinely
carry eight decimal places (fractional shares, crypto, dividend reinvestment)
and JSON numbers are IEEE-754 doubles on the other side of the wire — round
tripping 0.00000001 through a double is exactly the class of silent corruption
the integer-cents convention exists to prevent. The frontend formats it as text.
"""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class PositionAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    account_name: str
    account_mask: str | None
    account_subtype: str | None
    quantity: Decimal
    value_cents: int
    cost_basis_cents: int | None
    as_of_date: date

    @field_serializer("quantity")
    def _quantity(self, value: Decimal) -> str:
        return format(value.normalize(), "f")


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    security_id: uuid.UUID
    ticker_symbol: str | None
    name: str
    type: str
    asset_class: str
    is_cash_equivalent: bool

    quantity: Decimal
    price_cents: int | None
    price_as_of: date | None
    value_cents: int

    # Null when the custodian never reported a basis (ACATS transfers commonly
    # arrive without one). Null means unknown, never zero.
    cost_basis_cents: int | None
    gain_cents: int | None
    gain_bps: int | None
    day_change_cents: int | None

    weight_bps: int
    # Same position held across several accounts, split out.
    accounts: list[PositionAccountOut]

    @field_serializer("quantity")
    def _quantity(self, value: Decimal) -> str:
        return format(value.normalize(), "f")


class AllocationSliceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_class: str
    value_cents: int
    # Apportioned by largest remainder, so slices sum to exactly 10000.
    weight_bps: int
    position_count: int


class AccountAllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    name: str
    mask: str | None
    subtype: str | None
    value_cents: int
    cost_basis_cents: int | None
    gain_cents: int | None
    gain_bps: int | None
    day_change_cents: int | None
    weight_bps: int
    position_count: int
    as_of_date: date | None
    by_asset_class: list[AllocationSliceOut]


class PortfolioSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    as_of_date: date | None
    total_value_cents: int
    total_cost_basis_cents: int
    # Market value of only the positions that reported a basis — the correct
    # denominator for the gain percentage.
    cost_basis_value_cents: int
    unrealized_gain_cents: int
    unrealized_gain_bps: int | None
    # What fraction of portfolio value the gain figure actually covers.
    cost_basis_coverage_bps: int

    day_change_cents: int
    day_change_bps: int | None
    day_change_missing_count: int

    cash_value_cents: int
    invested_value_cents: int
    position_count: int
    account_count: int


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary: PortfolioSummaryOut
    positions: list[PositionOut]
    by_asset_class: list[AllocationSliceOut]
    by_account: list[AccountAllocationOut]


class AllocationOut(BaseModel):
    """Slimmer payload for the donut chart, which does not need the holdings."""

    model_config = ConfigDict(from_attributes=True)

    as_of_date: date | None
    total_value_cents: int
    by_asset_class: list[AllocationSliceOut]
    by_account: list[AccountAllocationOut]
