import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GoalCategory

from app.schemas import StrictRequest


class GoalAccountRefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    name: str
    mask: str | None
    type: str
    subtype: str | None
    # Signed the way it contributes to the goal: a paid-down loan balance is a
    # negative drag, not a positive pile of savings.
    balance_cents: int
    is_liability: bool


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str

    target_amount_cents: int
    # Live from linked accounts when the goal has any; the stored manual
    # baseline otherwise.
    current_amount_cents: int
    remaining_cents: int
    # Clamped to 0..10000 for progress bars.
    progress_bps: int
    # Unclamped, so an over-funded goal can still say 143%.
    raw_progress_bps: int

    target_date: date | None
    days_remaining: int | None
    months_remaining: int | None
    # Rounded up: a contribution rounded down misses by a cent every month and
    # still reports as on track.
    required_monthly_cents: int | None
    observed_monthly_cents: int | None
    # False when there was not enough history and the user's stated
    # contribution was used instead.
    observed_is_measured: bool

    projected_completion_date: date | None
    projected_vs_target_days: int | None
    status: str
    is_achieved: bool
    is_archived: bool

    monthly_contribution_cents: int | None
    notes: str | None
    linked_account_ids: list[uuid.UUID]
    linked_accounts: list[GoalAccountRefOut]


class GoalsSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    goal_count: int
    achieved_count: int
    off_track_count: int
    total_target_cents: int
    total_current_cents: int
    total_remaining_cents: int
    total_progress_bps: int
    total_required_monthly_cents: int


class GoalsReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary: GoalsSummaryOut
    goals: list[GoalOut]


class GoalCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=255)
    category: GoalCategory = GoalCategory.CUSTOM
    target_amount_cents: int = Field(gt=0)
    current_amount_cents: int = Field(0, ge=0)
    target_date: date | None = None
    monthly_contribution_cents: int | None = Field(None, ge=0)
    notes: str | None = None
    linked_account_ids: list[uuid.UUID] = []


class GoalUpdate(StrictRequest):
    """Every field optional — but `linked_account_ids` is a full replacement.

    Omitting it leaves the links alone; sending `[]` unlinks everything. There
    is no partial add/remove, so the client never has to reason about which
    links it holds a stale copy of.
    """

    name: str | None = Field(None, min_length=1, max_length=255)
    category: GoalCategory | None = None
    target_amount_cents: int | None = Field(None, gt=0)
    current_amount_cents: int | None = Field(None, ge=0)
    target_date: date | None = None
    monthly_contribution_cents: int | None = Field(None, ge=0)
    notes: str | None = None
    is_archived: bool | None = None
    linked_account_ids: list[uuid.UUID] | None = None
