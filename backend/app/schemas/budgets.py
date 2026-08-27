import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import StrictRequest


class BudgetUpsert(StrictRequest):
    category_id: uuid.UUID
    limit_cents: int = Field(ge=0)
    period: str = "MONTHLY"
    rollover_enabled: bool = False
    is_active: bool = True


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category_id: uuid.UUID
    limit_cents: int
    period: str
    effective_from: date
    rollover_enabled: bool
    is_active: bool


class BudgetLineOut(BaseModel):
    """One category row on the dashboard. Every money field is integer cents."""

    model_config = ConfigDict(from_attributes=True)

    budget_id: uuid.UUID | None
    category_id: uuid.UUID
    category_name: str
    category_slug: str
    parent_id: uuid.UUID | None

    limit_cents: int
    rollover_enabled: bool
    rollover_cents: int
    # limit + rollover — the number the user can actually spend this period.
    available_cents: int
    spent_cents: int
    remaining_cents: int
    # Run-rate forecast for the end of the period.
    projected_cents: int
    pacing_ratio_pct: int
    status: str
    elapsed_days: int
    total_days: int


class BudgetReportOut(BaseModel):
    period_start: date
    period_end: date
    period: str
    elapsed_days: int
    total_days: int
    total_limit_cents: int
    total_spent_cents: int
    total_projected_cents: int
    lines: list[BudgetLineOut]


class BudgetSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: uuid.UUID
    category_name: str
    category_slug: str
    # Oldest month first.
    monthly_spend_cents: list[int]
    median_cents: int
    suggested_limit_cents: int
    current_limit_cents: int | None


class SuggestionsOut(BaseModel):
    months_analyzed: int
    suggestions: list[BudgetSuggestionOut]


class ApplySuggestionsResult(BaseModel):
    created: int
    updated: int
    skipped: int
