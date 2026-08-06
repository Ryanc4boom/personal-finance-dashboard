"""Pending -> posted matching heuristic.

Banks issue a pending authorisation, then days later a *separate* posted
transaction with a brand new provider id. Naive ingestion keeps both and the
ledger double-counts ("ghost" transactions).

Plaid usually tells us the link via `pending_transaction_id`, and that is always
preferred. But it is absent for some institutions, and absent entirely for other
aggregators, so we fall back to a deliberately conservative heuristic:

    same account
    AND |amount difference| <= 1 cent
    AND |date difference| <= 4 days
    AND one normalised description contains the other

All four must hold. A false positive silently erases a real transaction from the
ledger, which is far worse than leaving a duplicate visible, so this errs toward
not matching. When several candidates qualify we take the closest by date, then
by amount, so the most plausible pair wins.
"""

import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction

AMOUNT_TOLERANCE_CENTS = 1
DATE_TOLERANCE_DAYS = 4
MIN_COMPARABLE_LENGTH = 4

_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")
# Card-processor noise that differs between the pending and posted record.
# Digit runs are always stripped: store numbers, terminal ids and auth codes
# routinely differ between the pending and posted copy of the same purchase
# ("WHOLE FOODS 102" -> "WHOLE FOODS MARKET"), and carry no matching signal.
_NOISE = re.compile(
    r"\b(POS|DEBIT|CREDIT|PURCHASE|PENDING|ACH|VISA|MASTERCARD|CHECKCARD|"
    r"RECURRING|PAYMENT|TST|SQ|PP|XX+|\d+)\b"
)


def normalize_description(value: str | None) -> str:
    if not value:
        return ""
    text = _NON_ALNUM.sub(" ", value.upper())
    text = _NOISE.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def descriptions_match(a: str | None, b: str | None) -> bool:
    left, right = normalize_description(a), normalize_description(b)
    if len(left) < MIN_COMPARABLE_LENGTH or len(right) < MIN_COMPARABLE_LENGTH:
        return False
    return left in right or right in left


def find_pending_match(
    db: Session,
    account_id,
    amount_cents: int,
    txn_date: date,
    description: str,
    exclude_provider_txn_id: str | None = None,
) -> Transaction | None:
    """Find the pending row that this posted transaction supersedes."""
    stmt = select(Transaction).where(
        Transaction.account_id == account_id,
        Transaction.is_pending.is_(True),
        Transaction.amount_cents.between(
            amount_cents - AMOUNT_TOLERANCE_CENTS,
            amount_cents + AMOUNT_TOLERANCE_CENTS,
        ),
        Transaction.date.between(
            txn_date - timedelta(days=DATE_TOLERANCE_DAYS),
            txn_date + timedelta(days=DATE_TOLERANCE_DAYS),
        ),
    )
    if exclude_provider_txn_id:
        stmt = stmt.where(Transaction.provider_txn_id != exclude_provider_txn_id)

    candidates = [
        c
        for c in db.scalars(stmt).all()
        if descriptions_match(description, c.description_raw)
        or descriptions_match(description, c.merchant_name)
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda c: (
            abs((c.date - txn_date).days),
            abs(c.amount_cents - amount_cents),
        )
    )
    return candidates[0]
