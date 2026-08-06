import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AMOUNT_MATCH_TYPES, TEXT_MATCH_TYPES

MatchType = Literal[
    "EXACT_MERCHANT", "DESCRIPTION_CONTAINS", "AMOUNT_EQUALS", "AMOUNT_RANGE", "ACCOUNT_ID"
]


class RuleBase(BaseModel):
    match_type: MatchType
    match_value: str | None = None
    amount_min_cents: int | None = None
    amount_max_cents: int | None = None
    target_category_id: uuid.UUID
    # Higher wins. See models/rule.py for the full ordering contract.
    priority: int = Field(100, ge=0, le=10_000)
    applies_retroactively: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def check_fields_for_match_type(self):
        """Mirror of the DB CHECK constraint, so the API returns 422 not 500."""
        if self.match_type in TEXT_MATCH_TYPES:
            if not (self.match_value or "").strip():
                raise ValueError(f"{self.match_type} requires a non-empty match_value")
        elif self.match_type in AMOUNT_MATCH_TYPES:
            if self.amount_min_cents is None:
                raise ValueError(f"{self.match_type} requires amount_min_cents")
            if self.match_type == "AMOUNT_RANGE":
                if self.amount_max_cents is None:
                    raise ValueError("AMOUNT_RANGE requires amount_max_cents")
                if self.amount_max_cents < self.amount_min_cents:
                    raise ValueError("amount_max_cents must be >= amount_min_cents")
        return self


class RuleCreate(RuleBase):
    pass


class RuleOut(RuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    target_category_name: str | None = None
    target_category_slug: str | None = None


class RuleApplyResult(BaseModel):
    """Counts are surfaced so the UI can be honest about what it touched."""

    matched: int
    changed: int
    skipped_user_categorized: int


class RuleCreateResult(BaseModel):
    rule: RuleOut
    applied: RuleApplyResult | None = None


class RecategorizeResult(BaseModel):
    changed: int
    skipped_user_categorized: int
