"""Budget tracking, pacing forecasts and trailing-median suggestions.

**What counts as spend.** A transaction contributes to a budget only if all of
these hold:

    the category's kind is EXPENSE        (income and transfers never count)
    AND NOT is_transfer                   (detected internal movements)
    AND NOT excluded_from_budget          (per-transaction user override)
    AND the account is not an investment account

The first and third conditions overlap on purpose. Category kind catches a
brokerage contribution that was categorised as a transfer but never paired;
`is_transfer` catches a paired movement that is still sitting in Uncategorized.
Either alone leaves a hole, and a hole here means a fictional expense.

Spend is computed as `-SUM(amount_cents)`, not as a sum of outflows only, so a
refund genuinely reduces the month's spend for its category rather than being
invisible. A category with more refunds than purchases yields negative spend,
which is correct and is displayed as such.

**Pacing.** The forecast is the spec's run rate::

    projected = spend / elapsed_days_in_period * total_days_in_period

which answers "if the rest of the month looks like the part I've lived through,
where do I land?". It is deliberately naive — it does not know that rent lands
on the 1st — so a large early fixed cost reads as over-pacing. That is the right
failure direction for a warning signal: it errs loud and early.

All money stays integer cents end to end; the one division is quantised with
ROUND_HALF_UP at the point of projection.
"""

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.models import Account, Budget, Category, Item, Transaction, User
from app.models.enums import BudgetPeriod, CategoryKind, PacingStatus

# Projected/limit ratios at which a category turns yellow, then red.
AT_RISK_RATIO = Decimal("1.00")
OVER_PACING_RATIO = Decimal("1.15")

# How far back rollover accumulation will walk, regardless of effective_from.
MAX_ROLLOVER_PERIODS = 24

SUGGESTION_MONTHS = 3
# Suggestions are rounded up to a round number; a limit of "$247.31" is noise.
SUGGESTION_ROUNDING_CENTS = 500


# --------------------------------------------------------------------------- #
# Period arithmetic
# --------------------------------------------------------------------------- #

def month_start(anchor: date) -> date:
    return anchor.replace(day=1)


def add_months(anchor: date, months: int) -> date:
    """Month arithmetic on the first of the month (day is always 1 here)."""
    total = anchor.year * 12 + (anchor.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def period_bounds(anchor: date, period: str = BudgetPeriod.MONTHLY.value) -> tuple[date, date]:
    """Inclusive [start, end] of the period containing `anchor`."""
    if period == BudgetPeriod.WEEKLY.value:
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)

    if period == BudgetPeriod.QUARTERLY.value:
        first_month = 3 * ((anchor.month - 1) // 3) + 1
        start = date(anchor.year, first_month, 1)
        return start, add_months(start, 3) - timedelta(days=1)

    if period == BudgetPeriod.YEARLY.value:
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31)

    start = month_start(anchor)
    return start, date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1])


def previous_period(start: date, period: str) -> date:
    if period == BudgetPeriod.WEEKLY.value:
        return start - timedelta(days=7)
    if period == BudgetPeriod.QUARTERLY.value:
        return add_months(start, -3)
    if period == BudgetPeriod.YEARLY.value:
        return date(start.year - 1, 1, 1)
    return add_months(start, -1)


def elapsed_and_total_days(start: date, end: date, today: date) -> tuple[int, int]:
    """Days lived through in the period, and its length.

    A period entirely in the past counts as fully elapsed; one entirely in the
    future counts as zero elapsed, which suppresses projection rather than
    dividing by zero.
    """
    total = (end - start).days + 1
    if today < start:
        return 0, total
    if today > end:
        return total, total
    return (today - start).days + 1, total


# --------------------------------------------------------------------------- #
# Category tree
# --------------------------------------------------------------------------- #

def category_descendants(db: Session, user: User) -> dict[uuid.UUID, set[uuid.UUID]]:
    """For each category, the set of ids whose spend rolls into it (incl. itself)."""
    categories = db.scalars(
        select(Category).where(
            (Category.user_id.is_(None)) | (Category.user_id == user.id)
        )
    ).all()

    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for c in categories:
        if c.parent_id is not None:
            children.setdefault(c.parent_id, []).append(c.id)

    return {c.id: {c.id, *children.get(c.id, ())} for c in categories}


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #

def _budgetable(user: User) -> Select:
    """Base query for transactions that may count toward a budget."""
    return (
        select(Transaction.category_id, func.sum(Transaction.amount_cents))
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Item.user_id == user.id,
            Transaction.is_transfer.is_(False),
            Transaction.excluded_from_budget.is_(False),
            Category.kind == CategoryKind.EXPENSE.value,
            Account.type != "investment",
        )
        .group_by(Transaction.category_id)
    )


def spend_by_category(
    db: Session, user: User, start: date, end: date
) -> dict[uuid.UUID, int]:
    """Net spend in cents per category (positive = money spent)."""
    rows = db.execute(
        _budgetable(user).where(and_(Transaction.date >= start, Transaction.date <= end))
    ).all()
    return {category_id: -int(total or 0) for category_id, total in rows if category_id}


def rolled_up_spend(
    raw: dict[uuid.UUID, int], descendants: dict[uuid.UUID, set[uuid.UUID]]
) -> dict[uuid.UUID, int]:
    return {
        category_id: sum(raw.get(child, 0) for child in subtree)
        for category_id, subtree in descendants.items()
    }


# --------------------------------------------------------------------------- #
# Pacing
# --------------------------------------------------------------------------- #

def project_spend(spend_cents: int, elapsed_days: int, total_days: int) -> int:
    """Run-rate projection, in integer cents."""
    if elapsed_days <= 0:
        return 0
    projected = (Decimal(spend_cents) * total_days) / Decimal(elapsed_days)
    return int(projected.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def pacing_status(spend_cents: int, projected_cents: int, available_cents: int) -> str:
    if available_cents <= 0:
        return PacingStatus.NO_LIMIT.value
    if spend_cents > available_cents:
        return PacingStatus.OVER_BUDGET.value

    ratio = Decimal(projected_cents) / Decimal(available_cents)
    if ratio > OVER_PACING_RATIO:
        return PacingStatus.OVER_PACING.value
    if ratio > AT_RISK_RATIO:
        return PacingStatus.AT_RISK.value
    return PacingStatus.ON_TRACK.value


@dataclass
class BudgetLine:
    budget_id: uuid.UUID | None
    category_id: uuid.UUID
    category_name: str
    category_slug: str
    parent_id: uuid.UUID | None
    limit_cents: int
    rollover_enabled: bool
    rollover_cents: int
    available_cents: int
    spent_cents: int
    remaining_cents: int
    projected_cents: int
    pacing_ratio_pct: int
    status: str
    elapsed_days: int
    total_days: int


@dataclass
class BudgetPeriodReport:
    period_start: date
    period_end: date
    period: str
    elapsed_days: int
    total_days: int
    total_limit_cents: int
    total_spent_cents: int
    total_projected_cents: int
    lines: list[BudgetLine] = field(default_factory=list)


def _rollover_for(
    db: Session,
    user: User,
    budget: Budget,
    current_start: date,
    descendants: dict[uuid.UUID, set[uuid.UUID]],
) -> int:
    """Unspent (or overspent) balance carried in from prior periods.

    Walks backwards from the current period to `effective_from`, capped at
    MAX_ROLLOVER_PERIODS. Uses today's limit for every historical period — see
    the Budget model docstring for why that trade was made.
    """
    if not budget.rollover_enabled:
        return 0

    subtree = descendants.get(budget.category_id, {budget.category_id})
    carried = 0
    cursor = previous_period(current_start, budget.period)

    for _ in range(MAX_ROLLOVER_PERIODS):
        if cursor < budget.effective_from:
            break
        start, end = period_bounds(cursor, budget.period)
        raw = spend_by_category(db, user, start, end)
        spent = sum(raw.get(cid, 0) for cid in subtree)
        carried += budget.limit_cents - spent
        cursor = previous_period(start, budget.period)

    return carried


def build_report(
    db: Session,
    user: User,
    anchor: date | None = None,
    period: str = BudgetPeriod.MONTHLY.value,
    today: date | None = None,
) -> BudgetPeriodReport:
    anchor = anchor or date.today()
    today = today or date.today()
    start, end = period_bounds(anchor, period)
    elapsed, total = elapsed_and_total_days(start, end, today)

    budgets = db.scalars(
        select(Budget).where(Budget.user_id == user.id, Budget.is_active.is_(True))
    ).all()

    descendants = category_descendants(db, user)
    raw = spend_by_category(db, user, start, end)
    categories = {
        c.id: c
        for c in db.scalars(
            select(Category).where(
                (Category.user_id.is_(None)) | (Category.user_id == user.id)
            )
        ).all()
    }

    report = BudgetPeriodReport(
        period_start=start,
        period_end=end,
        period=period,
        elapsed_days=elapsed,
        total_days=total,
        total_limit_cents=0,
        total_spent_cents=0,
        total_projected_cents=0,
    )

    for budget in budgets:
        category = categories.get(budget.category_id)
        if category is None:
            continue

        subtree = descendants.get(budget.category_id, {budget.category_id})
        spent = sum(raw.get(cid, 0) for cid in subtree)
        rollover = _rollover_for(db, user, budget, start, descendants)
        available = budget.limit_cents + rollover
        projected = project_spend(spent, elapsed, total)

        report.lines.append(
            BudgetLine(
                budget_id=budget.id,
                category_id=category.id,
                category_name=category.name,
                category_slug=category.slug,
                parent_id=category.parent_id,
                limit_cents=budget.limit_cents,
                rollover_enabled=budget.rollover_enabled,
                rollover_cents=rollover,
                available_cents=available,
                spent_cents=spent,
                remaining_cents=available - spent,
                projected_cents=projected,
                pacing_ratio_pct=(
                    int(
                        (Decimal(projected) / Decimal(available) * 100).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP
                        )
                    )
                    if available > 0
                    else 0
                ),
                status=pacing_status(spent, projected, available),
                elapsed_days=elapsed,
                total_days=total,
            )
        )
        report.total_limit_cents += budget.limit_cents
        report.total_spent_cents += spent
        report.total_projected_cents += projected

    report.lines.sort(key=lambda line: (-line.spent_cents, line.category_name))
    return report


# --------------------------------------------------------------------------- #
# Suggestions
# --------------------------------------------------------------------------- #

def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def _round_up(cents: int, step: int = SUGGESTION_ROUNDING_CENTS) -> int:
    if cents <= 0:
        return 0
    return ((cents + step - 1) // step) * step


@dataclass
class BudgetSuggestion:
    category_id: uuid.UUID
    category_name: str
    category_slug: str
    monthly_spend_cents: list[int]
    median_cents: int
    suggested_limit_cents: int
    current_limit_cents: int | None


def suggest_limits(
    db: Session, user: User, today: date | None = None, months: int = SUGGESTION_MONTHS
) -> list[BudgetSuggestion]:
    """Trailing-median monthly spend per parent category.

    The median of the last `months` *complete* months — the current partial
    month is excluded, since a suggestion built from a half-finished month
    lowballs every limit. Median rather than mean so one holiday or one repair
    bill does not permanently inflate the recommendation.

    Suggestions are made at parent level only. Budgeting a parent and its child
    simultaneously would double-count the child's spend in any total, and parent
    level is the realistic starting point the user can refine later.
    """
    today = today or date.today()
    descendants = category_descendants(db, user)

    categories = {
        c.id: c
        for c in db.scalars(
            select(Category).where(
                (Category.user_id.is_(None)) | (Category.user_id == user.id)
            )
        ).all()
    }
    existing = {
        b.category_id: b.limit_cents
        for b in db.scalars(select(Budget).where(Budget.user_id == user.id)).all()
    }

    # Walk back from the last complete month.
    period_starts = []
    cursor = add_months(month_start(today), -1)
    for _ in range(months):
        period_starts.append(cursor)
        cursor = add_months(cursor, -1)

    monthly: list[dict[uuid.UUID, int]] = []
    for start in period_starts:
        _, end = period_bounds(start, BudgetPeriod.MONTHLY.value)
        monthly.append(spend_by_category(db, user, start, end))

    suggestions: list[BudgetSuggestion] = []
    for category_id, category in categories.items():
        if category.parent_id is not None or category.kind != CategoryKind.EXPENSE.value:
            continue

        subtree = descendants.get(category_id, {category_id})
        # Months with no spend contribute a real zero, so an irregular category
        # gets a median of 0 and is dropped below rather than being budgeted as
        # if it recurred monthly.
        series = [
            max(0, sum(month.get(cid, 0) for cid in subtree)) for month in monthly
        ]
        median = _median(series)
        if median <= 0:
            continue

        suggestions.append(
            BudgetSuggestion(
                category_id=category_id,
                category_name=category.name,
                category_slug=category.slug,
                monthly_spend_cents=list(reversed(series)),
                median_cents=median,
                suggested_limit_cents=_round_up(median),
                current_limit_cents=existing.get(category_id),
            )
        )

    suggestions.sort(key=lambda s: -s.median_cents)
    return suggestions


def apply_suggestions(
    db: Session, user: User, today: date | None = None, overwrite: bool = False
) -> dict:
    """Create budgets from the trailing-median suggestions.

    Existing limits are preserved unless `overwrite` is set — the button is a
    starting point for someone with no budgets, not a reset of tuned ones.
    """
    today = today or date.today()
    suggestions = suggest_limits(db, user, today=today)
    effective = month_start(today)

    created = updated = skipped = 0
    for suggestion in suggestions:
        budget = db.scalar(
            select(Budget).where(
                Budget.user_id == user.id, Budget.category_id == suggestion.category_id
            )
        )
        if budget is None:
            db.add(
                Budget(
                    user_id=user.id,
                    category_id=suggestion.category_id,
                    limit_cents=suggestion.suggested_limit_cents,
                    period=BudgetPeriod.MONTHLY.value,
                    effective_from=effective,
                )
            )
            created += 1
        elif overwrite:
            budget.limit_cents = suggestion.suggested_limit_cents
            updated += 1
        else:
            skipped += 1

    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}
