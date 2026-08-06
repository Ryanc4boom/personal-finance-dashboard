"""Money and sign normalisation at the ingestion boundary.

Two rules are enforced here and nowhere else:

1. Integer cents. Provider JSON gives us floats (`12.34`). We convert via Decimal
   built from the *string* form — `Decimal(12.34)` would inherit the binary
   float error, `Decimal("12.34")` does not.

2. One internal sign convention. Plaid reports a purchase as a POSITIVE amount
   (money leaving the account) and a deposit as NEGATIVE. That is the opposite of
   how anyone reads a ledger, so we flip it exactly once, here:

       amount_cents > 0  => INFLOW  (money into the account)
       amount_cents < 0  => OUTFLOW (money out of the account)

   Downstream code may sum `amount_cents` blindly and never needs to branch on
   account type or direction.
"""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.models.enums import TransactionDirection

TRANSFER_CATEGORIES = {"TRANSFER_IN", "TRANSFER_OUT"}


def to_cents(amount: Any) -> int | None:
    """Convert a provider currency value to integer minor units."""
    if amount is None:
        return None
    quantised = (Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(quantised)


def normalize_amount_cents(plaid_amount: Any) -> int:
    """Flip Plaid's sign convention into the internal one."""
    cents = to_cents(plaid_amount)
    if cents is None:
        raise ValueError("transaction amount is required")
    return -cents


def direction_for(amount_cents: int) -> str:
    return (
        TransactionDirection.INFLOW.value
        if amount_cents >= 0
        else TransactionDirection.OUTFLOW.value
    )


def category_of(payload: dict) -> str | None:
    pfc = payload.get("personal_finance_category") or {}
    detailed = pfc.get("detailed")
    if detailed:
        return str(detailed)[:64]
    legacy = payload.get("category_id")
    return str(legacy)[:64] if legacy else None


def is_transfer(payload: dict) -> bool:
    pfc = payload.get("personal_finance_category") or {}
    return str(pfc.get("primary") or "") in TRANSFER_CATEGORIES


def clean_description(payload: dict) -> str:
    return (
        payload.get("name")
        or payload.get("original_description")
        or payload.get("merchant_name")
        or "(no description)"
    )


def as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def effective_dates(payload: dict) -> tuple[date, date | None]:
    """Return (date, posted_date) for a Plaid transaction payload.

    `date` is the date the transaction *happened* — we prefer `authorized_date`
    because it stays constant when a pending transaction later posts, so the row
    does not jump around the ledger on promotion. `posted_date` is populated only
    once the transaction has actually settled.
    """
    authorized = as_date(payload.get("authorized_date"))
    reported = as_date(payload.get("date"))
    pending = bool(payload.get("pending"))

    txn_date = authorized or reported
    if txn_date is None:
        raise ValueError("transaction is missing both authorized_date and date")
    return txn_date, (None if pending else reported)


def jsonable(value: Any) -> Any:
    """Recursively make a Plaid model dict safe for JSONB storage."""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    return str(value)
