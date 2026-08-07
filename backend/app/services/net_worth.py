"""Net worth: daily assets minus liabilities, reconstructed backwards through history.

**The sign of a balance is derived from the account type, never trusted.** Plaid
reports a credit card's `balances.current` as a *positive* amount owed;
`seeds/demo.py` stores the same card as a negative number. Both conventions are
live in this database right now, so `SUM(current_balance_cents)` is not a net
worth — it is a number whose meaning depends on where each row came from.
`signed_balance_cents` therefore keys off `account.type` and normalises the
magnitude, which is correct under either convention. (The underlying
inconsistency in `ingestion.upsert_account` is a real Phase 1 bug and worth
fixing at the source; this function is not a substitute for that, it is what
makes net worth correct in the meantime.)

**History is reconstructed, and the record says so.** There is no stored
balance history for most days — `balance_snapshot` is only written when a sync
runs. So a past balance is derived by walking today's balance backwards through
the ledger:

    balance(d) = balance(d + 1) - net_flow(d + 1)

which is exact for any account whose every movement is in the `transaction`
table. Each snapshot records whether it was `ACTUAL` (a real stored balance for
that day) or `RECONSTRUCTED`, so the chart can be honest about where
observation ends and inference begins rather than presenting both as the same
kind of fact.

**Investment accounts are valued from holdings, never from both sources.** An
investment account carries a `current_balance_cents` *and* a set of holdings
that sum to the same money. Adding both would double the portfolio. Holdings win
when a dated snapshot exists, because they are the only source that captures
market movement; the account balance is the fallback. Crucially, investment
accounts are *not* walked backwards through `transaction`: Plaid's
`/transactions/sync` does not return trades, so the ledger has no record of what
moved inside a brokerage, and applying the walk there would drift further from
the truth with every step back in time. Their value is held flat between
holding snapshots instead — flat is wrong, but it is *visibly* wrong and does
not compound.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    BalanceSnapshot,
    Holding,
    Item,
    NetWorthSnapshot,
    Transaction,
    User,
)
from app.models.enums import LIABILITY_ACCOUNT_TYPES, AccountType

# Ranges the history endpoint accepts, in days. `None` is "all of it".
RANGE_DAYS: dict[str, int | None] = {
    "1M": 30,
    "3M": 90,
    "6M": 182,
    "1Y": 365,
    "ALL": None,
}

# A backfill has to stop somewhere; five years is well past any useful chart and
# stops a corrupt early transaction date from generating a decade of rows.
MAX_BACKFILL_DAYS = 1825


def signed_balance_cents(account: Account) -> int:
    """Balance signed by account type: assets positive, liabilities negative.

    Derived from `account.type` rather than the stored sign — see the module
    docstring for why the stored sign is not reliable.
    """
    balance = account.current_balance_cents
    if balance is None:
        return 0
    if account.type in LIABILITY_ACCOUNT_TYPES:
        return -abs(balance)
    return balance


def _bucket_for(account: Account) -> str:
    if account.type == AccountType.depository.value:
        return "cash"
    if account.type == AccountType.investment.value:
        return "investments"
    if account.type == AccountType.credit.value:
        return "credit"
    if account.type == AccountType.loan.value:
        return "loans"
    return "other"


@dataclass
class NetWorthPoint:
    date: date
    total_assets_cents: int
    total_liabilities_cents: int
    net_worth_cents: int
    source: str
    breakdown: dict = field(default_factory=dict)


@dataclass
class BackfillResult:
    start_date: str | None = None
    end_date: str | None = None
    days_computed: int = 0
    snapshots_created: int = 0
    snapshots_updated: int = 0
    accounts_considered: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# --------------------------------------------------------------------------- #
# Series construction
# --------------------------------------------------------------------------- #

def _user_accounts(db: Session, user: User) -> list[Account]:
    return list(
        db.scalars(
            select(Account)
            .join(Item, Account.item_id == Item.id)
            .where(Item.user_id == user.id, Account.is_active.is_(True))
            .order_by(Account.name.asc())
        ).all()
    )


def _daily_flows(
    db: Session, account_ids: list[uuid.UUID], start: date, end: date
) -> dict[tuple[uuid.UUID, date], int]:
    """Net signed ledger movement per account per day.

    Pending rows are included: a pending charge is money the user has already
    committed, and excluding it would make every net worth chart tick upward
    for two days after each purchase and then drop.
    """
    if not account_ids:
        return {}

    rows = db.execute(
        select(
            Transaction.account_id,
            Transaction.date,
            func.sum(Transaction.amount_cents),
        )
        .where(
            Transaction.account_id.in_(account_ids),
            Transaction.date > start,
            Transaction.date <= end,
        )
        .group_by(Transaction.account_id, Transaction.date)
    ).all()
    return {(account_id, on): int(total) for account_id, on, total in rows}


def _holdings_series(
    db: Session, account_ids: list[uuid.UUID], start: date, end: date
) -> dict[uuid.UUID, list[tuple[date, int]]]:
    """Per investment account, its dated holding totals in ascending date order.

    Reaches back before `start` deliberately: valuing the first day of the chart
    needs the most recent snapshot at or before it, which may predate the window.
    """
    if not account_ids:
        return {}

    rows = db.execute(
        select(
            Holding.account_id,
            Holding.as_of_date,
            func.sum(Holding.institution_value_cents),
        )
        .where(Holding.account_id.in_(account_ids), Holding.as_of_date <= end)
        .group_by(Holding.account_id, Holding.as_of_date)
        .order_by(Holding.account_id, Holding.as_of_date)
    ).all()

    series: dict[uuid.UUID, list[tuple[date, int]]] = {}
    for account_id, on, total in rows:
        series.setdefault(account_id, []).append((on, int(total)))
    return series


def _value_at(series: list[tuple[date, int]], on: date) -> int | None:
    """Most recent value at or before `on`; None if the series starts later.

    Linear scan from the end. The series is one entry per sync per account —
    tens to hundreds of rows — so an index would cost more than it saves.
    """
    for snapshot_date, value in reversed(series):
        if snapshot_date <= on:
            return value
    return None


def _actual_balances(
    db: Session, account_ids: list[uuid.UUID], start: date, end: date
) -> dict[tuple[uuid.UUID, date], int]:
    """Stored balance history, where any exists. Marks a day as observed."""
    if not account_ids:
        return {}

    rows = db.execute(
        select(BalanceSnapshot.account_id, BalanceSnapshot.date, BalanceSnapshot.balance_cents)
        .where(
            BalanceSnapshot.account_id.in_(account_ids),
            BalanceSnapshot.date >= start,
            BalanceSnapshot.date <= end,
        )
    ).all()
    return {(account_id, on): int(balance) for account_id, on, balance in rows}


def build_series(
    db: Session,
    user: User,
    start: date,
    end: date,
) -> list[NetWorthPoint]:
    """Daily net worth from `start` to `end`, walking balances backwards from today.

    One pass, one query per data source. Recomputing a single day at a time
    would re-read the whole ledger per day and turn a two-year chart into
    hundreds of full table scans.
    """
    accounts = _user_accounts(db, user)
    if not accounts:
        return []

    ledger_accounts = [
        a for a in accounts if a.type != AccountType.investment.value
    ]
    investment_accounts = [
        a for a in accounts if a.type == AccountType.investment.value
    ]

    ledger_ids = [a.id for a in ledger_accounts]
    investment_ids = [a.id for a in investment_accounts]

    flows = _daily_flows(db, ledger_ids, start, end)
    stored = _actual_balances(db, ledger_ids, start, end)
    holdings = _holdings_series(db, investment_ids, start, end)

    # Seed the walk with each account's signed balance as of `end`.
    running = {a.id: signed_balance_cents(a) for a in ledger_accounts}
    by_type = {a.id: a for a in accounts}

    points: list[NetWorthPoint] = []
    cursor = end

    while cursor >= start:
        buckets = {
            "cash": 0,
            "investments": 0,
            "other": 0,
            "credit": 0,
            "loans": 0,
        }
        observed = True

        for account_id, balance in running.items():
            account = by_type[account_id]
            actual = stored.get((account_id, cursor))
            if actual is not None:
                # A real recorded balance beats the reconstruction and re-anchors
                # the walk, so drift cannot accumulate past a known-good day.
                balance = -abs(actual) if account.type in LIABILITY_ACCOUNT_TYPES else actual
                running[account_id] = balance
            else:
                observed = False

            bucket = _bucket_for(account)
            buckets[bucket] += balance

        for account in investment_accounts:
            series = holdings.get(account.id) or []
            value = _value_at(series, cursor)
            if value is None:
                # No holdings at or before this date. Fall back to the account
                # balance rather than dropping the account to zero, which would
                # put a cliff in the chart that never happened.
                value = signed_balance_cents(account)
                observed = False
            buckets["investments"] += value

        assets = buckets["cash"] + buckets["investments"] + buckets["other"]
        # Buckets hold liabilities negative; the stored column is a magnitude.
        liabilities = -(buckets["credit"] + buckets["loans"])

        points.append(
            NetWorthPoint(
                date=cursor,
                total_assets_cents=assets,
                total_liabilities_cents=liabilities,
                net_worth_cents=assets - liabilities,
                source="ACTUAL" if observed else "RECONSTRUCTED",
                breakdown={
                    "cash_cents": buckets["cash"],
                    "investments_cents": buckets["investments"],
                    "other_assets_cents": buckets["other"],
                    "credit_cents": -buckets["credit"],
                    "loans_cents": -buckets["loans"],
                },
            )
        )

        # Step back a day: undo the flows that landed on the day just recorded.
        for account_id in running:
            running[account_id] -= flows.get((account_id, cursor), 0)
        cursor -= timedelta(days=1)

    points.reverse()
    return points


# --------------------------------------------------------------------------- #
# Snapshot persistence
# --------------------------------------------------------------------------- #

def earliest_activity(db: Session, user: User) -> date | None:
    """First day this user has any record of — where a full backfill starts."""
    first_txn = db.scalar(
        select(func.min(Transaction.date))
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(Item.user_id == user.id)
    )
    first_holding = db.scalar(
        select(func.min(Holding.as_of_date))
        .select_from(Holding)
        .join(Account, Holding.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(Item.user_id == user.id)
    )
    candidates = [d for d in (first_txn, first_holding) if d is not None]
    return min(candidates) if candidates else None


def backfill(
    db: Session,
    user: User,
    start: date | None = None,
    end: date | None = None,
) -> BackfillResult:
    """Recompute and persist daily snapshots across a window.

    Idempotent: snapshots are upserted on `(user, snapshot_date)`, so re-running
    after a sync corrects history in place rather than duplicating it. Explicit
    by design — reading the chart never triggers this, because a read that
    silently rewrites two years of stored history is not a read.
    """
    result = BackfillResult()
    end = end or date.today()
    if start is None:
        start = earliest_activity(db, user) or end
    start = max(start, end - timedelta(days=MAX_BACKFILL_DAYS))

    if start > end:
        result.warnings.append("start date is after end date; nothing to compute")
        return result

    result.start_date = start.isoformat()
    result.end_date = end.isoformat()
    result.accounts_considered = len(_user_accounts(db, user))

    points = build_series(db, user, start, end)
    result.days_computed = len(points)

    existing = {
        row.snapshot_date: row
        for row in db.scalars(
            select(NetWorthSnapshot).where(
                NetWorthSnapshot.user_id == user.id,
                NetWorthSnapshot.snapshot_date >= start,
                NetWorthSnapshot.snapshot_date <= end,
            )
        ).all()
    }

    for point in points:
        snapshot = existing.get(point.date)
        if snapshot is None:
            snapshot = NetWorthSnapshot(user_id=user.id, snapshot_date=point.date)
            db.add(snapshot)
            result.snapshots_created += 1
        else:
            result.snapshots_updated += 1

        snapshot.total_assets_cents = point.total_assets_cents
        snapshot.total_liabilities_cents = point.total_liabilities_cents
        snapshot.net_worth_cents = point.net_worth_cents
        snapshot.breakdown_json = point.breakdown
        snapshot.source = point.source

    db.commit()
    return result


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

@dataclass
class NetWorthChange:
    start_cents: int
    end_cents: int
    delta_cents: int
    delta_bps: int | None


@dataclass
class AccountBreakdownRow:
    account_id: uuid.UUID
    name: str
    mask: str | None
    type: str
    subtype: str | None
    bucket: str
    # Signed: assets positive, liabilities negative.
    balance_cents: int
    is_liability: bool


@dataclass
class NetWorthHistory:
    range_key: str
    start_date: date | None
    end_date: date | None
    current_assets_cents: int
    current_liabilities_cents: int
    current_net_worth_cents: int
    change: NetWorthChange | None
    points: list[NetWorthPoint] = field(default_factory=list)
    accounts: list[AccountBreakdownRow] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)


def account_breakdown(db: Session, user: User) -> list[AccountBreakdownRow]:
    """Current per-account position, assets first then liabilities.

    Investment accounts report their holdings total when one exists, matching
    what the series does, so this table and the chart cannot disagree.
    """
    rows: list[AccountBreakdownRow] = []
    for account in _user_accounts(db, user):
        if account.type == AccountType.investment.value:
            total = db.scalar(
                select(func.sum(Holding.institution_value_cents)).where(
                    Holding.account_id == account.id,
                    Holding.as_of_date
                    == select(func.max(Holding.as_of_date))
                    .where(Holding.account_id == account.id)
                    .scalar_subquery(),
                )
            )
            balance = int(total) if total is not None else signed_balance_cents(account)
        else:
            balance = signed_balance_cents(account)

        rows.append(
            AccountBreakdownRow(
                account_id=account.id,
                name=account.name,
                mask=account.mask,
                type=account.type,
                subtype=account.subtype,
                bucket=_bucket_for(account),
                balance_cents=balance,
                is_liability=account.type in LIABILITY_ACCOUNT_TYPES,
            )
        )

    rows.sort(key=lambda r: (r.is_liability, -abs(r.balance_cents)))
    return rows


def history(
    db: Session,
    user: User,
    range_key: str = "1Y",
    end: date | None = None,
) -> NetWorthHistory:
    """Stored snapshots over a range, plus today's live position.

    Reads `net_worth_snapshot` rather than recomputing: the chart is the hot
    path and the snapshots are what `backfill` exists to maintain. The final
    point is recomputed live so today is never stale between backfills.
    """
    end = end or date.today()
    window = RANGE_DAYS.get(range_key, RANGE_DAYS["1Y"])

    stmt = select(NetWorthSnapshot).where(
        NetWorthSnapshot.user_id == user.id, NetWorthSnapshot.snapshot_date <= end
    )
    if window is not None:
        stmt = stmt.where(NetWorthSnapshot.snapshot_date >= end - timedelta(days=window))

    snapshots = list(
        db.scalars(stmt.order_by(NetWorthSnapshot.snapshot_date.asc())).all()
    )

    points = [
        NetWorthPoint(
            date=s.snapshot_date,
            total_assets_cents=s.total_assets_cents,
            total_liabilities_cents=s.total_liabilities_cents,
            net_worth_cents=s.net_worth_cents,
            source=s.source,
            breakdown=s.breakdown_json or {},
        )
        for s in snapshots
    ]

    # Today, computed live — a chart whose last point silently lags the account
    # page by however long it has been since the last backfill is a bug report
    # waiting to happen.
    live = build_series(db, user, end, end)
    current = live[0] if live else None

    if current is not None:
        if points and points[-1].date == current.date:
            points[-1] = current
        else:
            points.append(current)

    change = None
    if len(points) >= 2:
        first, last = points[0], points[-1]
        delta = last.net_worth_cents - first.net_worth_cents
        # Percentage against a negative or zero starting net worth is not
        # meaningful — "up 40% from -$12,000" says nothing — so it is withheld
        # rather than rendered as a confident-looking wrong number.
        base = first.net_worth_cents
        change = NetWorthChange(
            start_cents=first.net_worth_cents,
            end_cents=last.net_worth_cents,
            delta_cents=delta,
            delta_bps=round(delta * 10_000 / base) if base > 0 else None,
        )

    return NetWorthHistory(
        range_key=range_key,
        start_date=points[0].date if points else None,
        end_date=points[-1].date if points else None,
        current_assets_cents=current.total_assets_cents if current else 0,
        current_liabilities_cents=current.total_liabilities_cents if current else 0,
        current_net_worth_cents=current.net_worth_cents if current else 0,
        change=change,
        points=points,
        accounts=account_breakdown(db, user),
        breakdown=current.breakdown if current else {},
    )
