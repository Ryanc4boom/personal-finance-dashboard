"""Financial goals: progress, required contribution and projected completion.

**Progress is measured, not stored, whenever it can be.** A goal linked to real
accounts reads its balance from those accounts every time it is rendered.
`financial_goal.current_amount_cents` is only consulted for goals with no
linked accounts — something the user is tracking that the platform cannot see.
A cached copy of a linked balance is stale the moment the next transaction
lands, and a progress bar that quietly shows last week's number is worse than
one that admits it has no data.

**Two different rates, and they answer different questions.**

* *Required* — what must be saved each month to hit the target by its date.
  Pure arithmetic over the remaining gap and the remaining time.
* *Observed* — what actually has been saved, measured over a trailing
  three-month window of real balance movement.

The gap between them is the entire value of this view. Reporting only the
required figure produces a plan that never notices it is not being followed;
reporting only the observed figure produces a projection with no target to miss.

**A liability-backed goal counts paying it down as progress.** Linking a
mortgage to a "pay off the house" goal must treat the balance shrinking as
progress toward the target, so linked balances use the same type-derived
signing as net worth and a goal made only of liabilities is scored on debt
reduction rather than reporting a permanently negative balance.

Rounding is integer throughout, and the required contribution rounds *up*: a
figure rounded down is one that provably misses the target, by a cent, every
month, forever.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    FinancialGoal,
    FinancialGoalAccount,
    Holding,
    Item,
    Transaction,
    User,
)
from app.models.enums import LIABILITY_ACCOUNT_TYPES, AccountType, GoalStatus
from app.services import budgets as budget_service
from app.services.net_worth import signed_balance_cents

# Average days per month (30.4375), scaled by 100 so month arithmetic stays in
# integers. Used only to convert a rate between "per month" and "per day".
AVG_MONTH_DAYS_X100 = 3044

# How far back the observed savings rate looks. Three months is long enough to
# absorb one unusual month and short enough to still describe current behaviour.
OBSERVED_WINDOW_MONTHS = 3

# Observed pace this far below the required pace is AT_RISK rather than
# OFF_TRACK — the same "slightly over vs well over" distinction Phase 2's budget
# pacing draws, so the two views grade on a consistent curve.
AT_RISK_FLOOR_BPS = 8_000  # 80% of required

# A projection further out than this is not a forecast, it is a rounding error
# in a savings rate multiplied by a very large number.
MAX_PROJECTION_DAYS = 365 * 50


def _ceil_div(numerator: int, denominator: int) -> int:
    if denominator == 0:
        return 0
    return -(-numerator // denominator)


# --------------------------------------------------------------------------- #
# Backing balances
# --------------------------------------------------------------------------- #

def _linked_accounts(db: Session, goal: FinancialGoal) -> list[Account]:
    return list(
        db.scalars(
            select(Account)
            .join(FinancialGoalAccount, FinancialGoalAccount.account_id == Account.id)
            .where(FinancialGoalAccount.goal_id == goal.id)
            .order_by(Account.name.asc())
        ).all()
    )


def _account_value_cents(db: Session, account: Account) -> int:
    """Current signed value, valuing investment accounts from their holdings.

    Mirrors services/net_worth.account_breakdown so a goal backed by a brokerage
    reports the same number the net worth page does.
    """
    if account.type == AccountType.investment.value:
        latest = db.scalar(
            select(func.max(Holding.as_of_date)).where(Holding.account_id == account.id)
        )
        if latest is not None:
            total = db.scalar(
                select(func.sum(Holding.institution_value_cents)).where(
                    Holding.account_id == account.id, Holding.as_of_date == latest
                )
            )
            if total is not None:
                return int(total)
    return signed_balance_cents(account)


def _backing_value_cents(
    db: Session, accounts: list[Account], target_cents: int
) -> int:
    """Progress contributed by the linked accounts.

    Balances are summed with net worth's signing — assets positive, liabilities
    negative — so a goal backed by "savings minus the card I am carrying" nets
    correctly without a special case.

    **A goal backed only by liabilities is scored on debt retired**, which is
    not the same as the balance itself. `target` for such a goal is the debt to
    eliminate, so progress is `target - outstanding`: zero while the full
    balance is owed, complete when it reaches zero. Reporting the outstanding
    balance as progress instead would mark a brand-new loan as 100% achieved on
    the day it was taken out and drop it to 0% once it was paid off — the exact
    inverse of the truth.
    """
    total = 0
    for account in accounts:
        value = _account_value_cents(db, account)
        total += value

    if all(account.type in LIABILITY_ACCOUNT_TYPES for account in accounts):
        # `total` is negative here, so this counts down the outstanding debt.
        # Continuous past zero: overpaying a loan pushes progress past target
        # rather than jumping.
        return target_cents + total
    return total


def _balance_change_cents(
    db: Session, accounts: list[Account], since: date, today: date
) -> tuple[int, bool]:
    """Movement in the backing balance since `since`.

    Returns `(delta_cents, measurable)`. Walks the ledger the same way
    services/net_worth does — today's balance minus the flows since — and
    reports `measurable=False` when no account has enough history to support the
    claim, so a goal opened yesterday does not project from three days of noise.
    """
    if not accounts:
        return 0, False

    ledger_ids = [a.id for a in accounts if a.type != AccountType.investment.value]
    investment_accounts = [a for a in accounts if a.type == AccountType.investment.value]

    delta = 0
    measurable = False

    if ledger_ids:
        rows = db.execute(
            select(Transaction.account_id, func.sum(Transaction.amount_cents))
            .where(
                Transaction.account_id.in_(ledger_ids),
                Transaction.date > since,
                Transaction.date <= today,
            )
            .group_by(Transaction.account_id)
        ).all()
        # Ledger flows are already signed the way progress moves: cash into a
        # savings account raises it, and cash into a loan account raises its
        # signed balance toward zero, which is the debt being retired. No
        # per-type inversion — the same inversion `_backing_value_cents` does
        # not do.
        for _account_id, total in rows:
            delta += int(total)
            measurable = True

    for account in investment_accounts:
        current = _account_value_cents(db, account)
        past = db.scalar(
            select(func.sum(Holding.institution_value_cents)).where(
                Holding.account_id == account.id,
                Holding.as_of_date
                == select(func.max(Holding.as_of_date))
                .where(Holding.account_id == account.id, Holding.as_of_date <= since)
                .scalar_subquery(),
            )
        )
        if past is not None:
            delta += current - int(past)
            measurable = True

    return delta, measurable


def _household_savings_cents(
    db: Session, user: User, since: date, today: date
) -> tuple[int, bool]:
    """Net cash saved across every depository account, for unlinked goals.

    Transfers are excluded: moving money between two of the user's own accounts
    nets to zero across the household but would otherwise be counted twice, once
    on each leg.
    """
    total = db.scalar(
        select(func.sum(Transaction.amount_cents))
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(
            Item.user_id == user.id,
            Account.type == AccountType.depository.value,
            Transaction.is_transfer.is_(False),
            Transaction.date > since,
            Transaction.date <= today,
        )
    )
    return (int(total), True) if total is not None else (0, False)


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #

@dataclass
class GoalAccountRef:
    account_id: uuid.UUID
    name: str
    mask: str | None
    type: str
    subtype: str | None
    balance_cents: int
    is_liability: bool


@dataclass
class GoalProgress:
    id: uuid.UUID
    name: str
    category: str
    target_amount_cents: int
    current_amount_cents: int
    remaining_cents: int
    # Capped at 10,000 so a bar cannot render past full, while
    # `raw_progress_bps` keeps the real figure for the label.
    progress_bps: int
    raw_progress_bps: int
    target_date: date | None
    days_remaining: int | None
    months_remaining: int | None

    required_monthly_cents: int | None
    observed_monthly_cents: int | None
    # True when the observed rate came from real balance movement rather than
    # the user's stated intent.
    observed_is_measured: bool
    projected_completion_date: date | None
    # Positive = projected to land after the target date.
    projected_vs_target_days: int | None

    status: str
    is_achieved: bool
    is_archived: bool
    monthly_contribution_cents: int | None
    notes: str | None
    linked_account_ids: list[uuid.UUID] = field(default_factory=list)
    linked_accounts: list[GoalAccountRef] = field(default_factory=list)


@dataclass
class GoalsSummary:
    goal_count: int
    achieved_count: int
    off_track_count: int
    total_target_cents: int
    total_current_cents: int
    total_remaining_cents: int
    total_progress_bps: int
    total_required_monthly_cents: int


@dataclass
class GoalsReport:
    summary: GoalsSummary
    goals: list[GoalProgress] = field(default_factory=list)


def build_progress(
    db: Session, user: User, goal: FinancialGoal, today: date | None = None
) -> GoalProgress:
    today = today or date.today()
    accounts = _linked_accounts(db, goal)

    if accounts:
        current = _backing_value_cents(db, accounts, goal.target_amount_cents)
    else:
        current = goal.current_amount_cents

    # A backing balance can legitimately go negative (a goal backed by a card
    # being carried). Progress is clamped at zero because a bar cannot be less
    # than empty, but `current_amount_cents` keeps the real, signed figure.
    remaining = max(0, goal.target_amount_cents - current)
    raw_bps = round(current * 10_000 / goal.target_amount_cents)
    achieved = current >= goal.target_amount_cents

    days_remaining: int | None = None
    months_remaining: int | None = None
    required_monthly: int | None = None

    if goal.target_date is not None:
        days_remaining = (goal.target_date - today).days
        if days_remaining > 0:
            months_remaining = max(1, round(days_remaining * 100 / AVG_MONTH_DAYS_X100))
            # Round up: a contribution rounded down misses the target by a cent
            # a month, every month, and still reports as on track.
            required_monthly = _ceil_div(remaining * AVG_MONTH_DAYS_X100, days_remaining * 100)
        else:
            months_remaining = 0
            # The date has passed. Whatever is left is needed now, not spread.
            required_monthly = remaining

    # ---- observed rate ---------------------------------------------------- #
    since = budget_service.add_months(today, -OBSERVED_WINDOW_MONTHS)
    if accounts:
        delta, measured = _balance_change_cents(db, accounts, since, today)
    else:
        delta, measured = _household_savings_cents(db, user, since, today)

    observed_monthly: int | None = None
    observed_is_measured = False
    if measured:
        window_days = max(1, (today - since).days)
        observed_monthly = round(delta * AVG_MONTH_DAYS_X100 / (window_days * 100))
        observed_is_measured = True

    # Fall back to what the user said they would contribute when there is no
    # usable history — a stated plan beats no projection at all.
    if (observed_monthly is None or observed_monthly <= 0) and goal.monthly_contribution_cents:
        observed_monthly = goal.monthly_contribution_cents
        observed_is_measured = False

    # ---- projection ------------------------------------------------------- #
    projected_date: date | None = None
    projected_vs_target: int | None = None

    if achieved:
        projected_date = today
    elif observed_monthly and observed_monthly > 0:
        days_needed = _ceil_div(remaining * AVG_MONTH_DAYS_X100, observed_monthly * 100)
        if days_needed <= MAX_PROJECTION_DAYS:
            projected_date = today + timedelta(days=days_needed)

    if projected_date is not None and goal.target_date is not None:
        projected_vs_target = (projected_date - goal.target_date).days

    # ---- status ----------------------------------------------------------- #
    if achieved:
        status = GoalStatus.ACHIEVED.value
    elif goal.target_date is None:
        status = GoalStatus.NO_DEADLINE.value
    elif required_monthly is None or required_monthly <= 0:
        status = GoalStatus.ON_TRACK.value
    elif observed_monthly is None or observed_monthly <= 0:
        # Nothing is being saved toward a goal that has a deadline.
        status = GoalStatus.OFF_TRACK.value
    else:
        ratio_bps = round(observed_monthly * 10_000 / required_monthly)
        if ratio_bps >= 10_000:
            status = GoalStatus.ON_TRACK.value
        elif ratio_bps >= AT_RISK_FLOOR_BPS:
            status = GoalStatus.AT_RISK.value
        else:
            status = GoalStatus.OFF_TRACK.value

    return GoalProgress(
        id=goal.id,
        name=goal.name,
        category=goal.category,
        target_amount_cents=goal.target_amount_cents,
        current_amount_cents=current,
        remaining_cents=remaining,
        progress_bps=max(0, min(10_000, raw_bps)),
        raw_progress_bps=raw_bps,
        target_date=goal.target_date,
        days_remaining=days_remaining,
        months_remaining=months_remaining,
        required_monthly_cents=required_monthly,
        observed_monthly_cents=observed_monthly,
        observed_is_measured=observed_is_measured,
        projected_completion_date=projected_date,
        projected_vs_target_days=projected_vs_target,
        status=status,
        is_achieved=achieved,
        is_archived=goal.is_archived,
        monthly_contribution_cents=goal.monthly_contribution_cents,
        notes=goal.notes,
        linked_account_ids=[a.id for a in accounts],
        linked_accounts=[
            GoalAccountRef(
                account_id=a.id,
                name=a.name,
                mask=a.mask,
                type=a.type,
                subtype=a.subtype,
                balance_cents=_account_value_cents(db, a),
                is_liability=a.type in LIABILITY_ACCOUNT_TYPES,
            )
            for a in accounts
        ],
    )


def build_report(
    db: Session,
    user: User,
    include_archived: bool = False,
    today: date | None = None,
) -> GoalsReport:
    today = today or date.today()
    stmt = select(FinancialGoal).where(FinancialGoal.user_id == user.id)
    if not include_archived:
        stmt = stmt.where(FinancialGoal.is_archived.is_(False))

    goals = db.scalars(
        stmt.order_by(
            # Achieved goals sink; the rest surface by urgency, with undated
            # goals last since they cannot be late.
            FinancialGoal.is_archived.asc(),
            FinancialGoal.target_date.is_(None).asc(),
            FinancialGoal.target_date.asc(),
            FinancialGoal.name.asc(),
        )
    ).all()

    progress = [build_progress(db, user, goal, today=today) for goal in goals]

    total_target = sum(p.target_amount_cents for p in progress)
    # Clamped per goal: overshooting one goal must not paper over a shortfall in
    # another when the household total is read as "how am I doing overall".
    total_current = sum(min(p.current_amount_cents, p.target_amount_cents) for p in progress)

    summary = GoalsSummary(
        goal_count=len(progress),
        achieved_count=sum(1 for p in progress if p.is_achieved),
        off_track_count=sum(1 for p in progress if p.status == GoalStatus.OFF_TRACK.value),
        total_target_cents=total_target,
        total_current_cents=total_current,
        total_remaining_cents=sum(p.remaining_cents for p in progress),
        total_progress_bps=(
            round(total_current * 10_000 / total_target) if total_target > 0 else 0
        ),
        total_required_monthly_cents=sum(p.required_monthly_cents or 0 for p in progress),
    )

    return GoalsReport(summary=summary, goals=progress)
