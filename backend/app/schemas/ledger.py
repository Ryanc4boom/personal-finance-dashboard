import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    mask: str | None
    type: str
    subtype: str | None
    current_balance_cents: int | None
    available_balance_cents: int | None
    credit_limit_cents: int | None
    is_active: bool
    institution_name: str | None = None


class AccountSummary(BaseModel):
    """Cents, always. The UI divides by 100 for display only."""

    depository_cash_cents: int
    credit_liabilities_cents: int
    net_cents: int
    accounts: list[AccountOut]


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    provider_txn_id: str
    amount_cents: int
    direction: str
    date: date
    posted_date: date | None
    description_raw: str
    merchant_name: str | None

    # Phase 2: normalisation + categorisation
    normalized_key: str | None = None
    merchant_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    # USER | RULE | MERCHANT | PROVIDER | UNCATEGORIZED — the UI shows this so a
    # user can tell an automated guess from their own decision.
    category_source: str | None = None
    provider_category: str | None = None
    transfer_pair_id: uuid.UUID | None = None

    is_pending: bool
    is_transfer: bool
    is_recurring: bool
    notes: str | None
    tags: list[str]
    excluded_from_budget: bool
    created_at: datetime

    account_name: str | None = None
    account_mask: str | None = None
    category_name: str | None = None
    category_slug: str | None = None
    merchant_display_name: str | None = None


class TransactionUpdate(BaseModel):
    """Every field here is user-owned; ingestion never overwrites them."""

    category_id: uuid.UUID | None = None
    notes: str | None = None
    tags: list[str] | None = None
    excluded_from_budget: bool | None = None
    is_transfer: bool | None = None


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


class TransferDetectResult(BaseModel):
    scanned: int
    pairs_found: int
    transactions_marked: int
