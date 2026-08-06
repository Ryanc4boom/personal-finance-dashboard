"""Internal transfer & credit-card payment detection.

The problem this solves is double counting. Paying $500 from checking to a
credit card produces two real transactions — a $500 outflow on checking and a
$500 inflow on the card. Neither is spending; the spending already happened when
the card was swiped. Left alone, the outflow inflates every spending total by
the size of the payment, which for most people is the largest single "expense"
in the month and pure fiction.

Pairing criteria, all required:

    different accounts, both owned by the user
    AND opposite signs
    AND |amount difference| <= 1 cent
    AND |date difference| <= 3 days

Matching is greedy and best-first: candidates are ranked by date closeness then
amount closeness, so when several $500 movements exist in the same week each one
pairs with its nearest counterpart rather than an arbitrary one. Each row is
consumed by at most one pair.

The pass is idempotent — only rows with `transfer_pair_id IS NULL` are
considered, so re-running never re-pairs or un-pairs settled history.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Item, Transaction, User

logger = logging.getLogger(__name__)

AMOUNT_TOLERANCE_CENTS = 1
DATE_TOLERANCE_DAYS = 3
DEFAULT_LOOKBACK_DAYS = 180


@dataclass
class TransferResult:
    scanned: int = 0
    pairs_found: int = 0
    transactions_marked: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _candidates(
    db: Session, user: User, start: date | None, end: date | None
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(
            Item.user_id == user.id,
            Transaction.transfer_pair_id.is_(None),
            Transaction.amount_cents != 0,
        )
        .order_by(Transaction.date.asc(), Transaction.id.asc())
    )
    if start is not None:
        stmt = stmt.where(Transaction.date >= start)
    if end is not None:
        stmt = stmt.where(Transaction.date <= end)
    return list(db.scalars(stmt).all())


def _mark(outflow: Transaction, inflow: Transaction) -> None:
    for txn, partner in ((outflow, inflow), (inflow, outflow)):
        txn.transfer_pair_id = partner.id
        txn.is_transfer = True
        # The spec's default: a detected transfer leaves the budget. The user
        # can still flip this back per-transaction in the ledger.
        txn.excluded_from_budget = True


def detect_transfers(
    db: Session,
    user: User,
    start_date: date | None = None,
    end_date: date | None = None,
    commit: bool = True,
) -> TransferResult:
    if start_date is None and end_date is None:
        start_date = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)

    rows = _candidates(db, user, start_date, end_date)
    result = TransferResult(scanned=len(rows))

    outflows = [t for t in rows if t.amount_cents < 0]
    inflows = [t for t in rows if t.amount_cents > 0]

    # Bucket inflows by absolute cents so the tolerance search is three dict
    # lookups instead of a scan over every inflow, per outflow.
    by_amount: dict[int, list[Transaction]] = {}
    for txn in inflows:
        by_amount.setdefault(abs(txn.amount_cents), []).append(txn)

    consumed: set[uuid.UUID] = set()

    for outflow in outflows:
        target = abs(outflow.amount_cents)
        candidates: list[Transaction] = []
        for delta in range(-AMOUNT_TOLERANCE_CENTS, AMOUNT_TOLERANCE_CENTS + 1):
            candidates.extend(by_amount.get(target + delta, ()))

        viable = [
            c
            for c in candidates
            if c.id not in consumed
            and c.account_id != outflow.account_id
            and abs((c.date - outflow.date).days) <= DATE_TOLERANCE_DAYS
        ]
        if not viable:
            continue

        viable.sort(
            key=lambda c: (
                abs((c.date - outflow.date).days),
                abs(abs(c.amount_cents) - target),
                c.id.bytes,  # deterministic tiebreak so reruns agree
            )
        )
        partner = viable[0]

        _mark(outflow, partner)
        consumed.add(partner.id)
        consumed.add(outflow.id)
        result.pairs_found += 1
        result.transactions_marked += 2

    if commit:
        db.commit()
    return result
