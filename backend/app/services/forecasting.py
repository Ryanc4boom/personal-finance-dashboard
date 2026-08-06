"""Rolling cash flow forecast.

Projects a daily cash balance forward from today:

    C(0) = current cash across depository accounts
    C(d) = C(d-1) + recurring inflows(d) - recurring outflows(d) - variable(d)

**Why depository only.** The question this view answers is "will I have money in
the bank on the 28th", which is about liquidity, not net worth. A credit card
balance is a liability that will be settled by a *future* transfer, and that
transfer is already modelled as a recurring outflow when it is one. Folding the
card balance into the starting point would subtract the same money twice, and
including an investment account would suggest cash that cannot be spent without
selling something.

**The double-counting trap.** Recurring bills and budget pacing overlap. Netflix
is a recurring outflow *and* part of the Subscriptions budget; rent is a
recurring outflow *and* part of the Housing budget. Subtracting both the modelled
bills and the full unspent budget drains the account twice for the same money and
turns every forecast pessimistic — which is worse than useless, because a warning
that is always on gets ignored. So the variable pool is netted::

    variable_pool = max(0, unspent_budget - recurring_outflows_still_to_come)

and only the remainder is spread across the days. The floor at zero matters: if a
month's recurring bills exceed its budgets, the answer is "no discretionary money
left", not "negative discretionary spending", which would show cash *rising*.

**Beyond the current month.** Budgets describe a month; a 90-day horizon spans
three. Later months reuse the same total budget, net of that month's recurring
bills, spread evenly. Without this the forecast would show a burn rate for 30
days and then a straight climb to the moon, which is the most misleading shape
this chart could possibly have.

Rounding: the per-day variable amount is integer-divided, and the remainder is
carried rather than dropped, so the days sum to the pool exactly. A 1-cent-a-day
leak over 90 days is small but it is the kind of drift that makes a user stop
trusting the number.
"""

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Item, RecurringStream, User
from app.models.enums import AccountType, BudgetPeriod, StreamStatus, TransactionDirection
from app.services import budgets as budget_service
from app.services.recurring import occurrences_between

# The spec's default liquidity floor.
DEFAULT_MIN_BALANCE_CENTS = 100_000  # $1,000

ALLOWED_HORIZONS = (30, 60, 90)


# --------------------------------------------------------------------------- #
# Starting balance
# --------------------------------------------------------------------------- #

def current_cash_cents(db: Session, user: User) -> tuple[int, list[dict]]:
    """Aggregate spendable cash, plus the per-account breakdown behind it.

    `available` is preferred over `current` when the bank reports it, because it
    already excludes holds — money that is displayed on the statement but cannot
    actually be spent tomorrow.
    """
    accounts = db.scalars(
        select(Account)
        .join(Item, Account.item_id == Item.id)
        .where(
            Item.user_id == user.id,
            Account.is_active.is_(True),
            Account.type == AccountType.depository.value,
        )
        .order_by(Account.name.asc())
    ).all()

    total = 0
    breakdown = []
    for account in accounts:
        balance = account.available_balance_cents
        if balance is None:
            balance = account.current_balance_cents or 0
        total += balance
        breakdown.append(
            {
                "account_id": account.id,
                "name": account.name,
                "mask": account.mask,
                "balance_cents": balance,
            }
        )
    return total, breakdown


# --------------------------------------------------------------------------- #
# Scheduled recurring movements
# --------------------------------------------------------------------------- #

@dataclass
class ForecastEvent:
    date: date
    stream_id: uuid.UUID
    display_name: str
    # Signed: positive is money in, matching the ledger's convention.
    amount_cents: int
    direction: str
    frequency: str
    is_subscription: bool


def scheduled_events(
    db: Session, user: User, start: date, end: date
) -> list[ForecastEvent]:
    """Expand every ACTIVE stream into its expected hits inside the window.

    PAUSED and CANCELLED streams contribute nothing. A paused stream is the one
    genuinely ambiguous case — it may resume — but forecasting money that is not
    currently moving would make the projection optimistic on income and
    pessimistic on spend at the same time.
    """
    streams = db.scalars(
        select(RecurringStream).where(
            RecurringStream.user_id == user.id,
            RecurringStream.status == StreamStatus.ACTIVE.value,
        )
    ).all()

    events: list[ForecastEvent] = []
    for stream in streams:
        sign = 1 if stream.direction == TransactionDirection.INFLOW.value else -1
        for on in occurrences_between(
            stream.next_expected_date, stream.frequency, start, end
        ):
            events.append(
                ForecastEvent(
                    date=on,
                    stream_id=stream.id,
                    display_name=stream.display_name,
                    amount_cents=sign * stream.expected_amount_cents,
                    direction=stream.direction,
                    frequency=stream.frequency,
                    is_subscription=stream.is_subscription,
                )
            )
    events.sort(key=lambda e: (e.date, e.display_name))
    return events


# --------------------------------------------------------------------------- #
# Variable (discretionary) spending
# --------------------------------------------------------------------------- #

def _month_end(anchor: date) -> date:
    return date(anchor.year, anchor.month, calendar.monthrange(anchor.year, anchor.month)[1])


def variable_daily_cents(
    db: Session,
    user: User,
    start: date,
    end: date,
    scheduled: list[ForecastEvent],
    today: date,
) -> dict[date, int]:
    """Per-day discretionary burn, netted against the recurring bills already modelled.

    The current month uses live budget data: whatever is left of each category's
    limit, after what has already been spent. Later months have no such data — the
    period has not started — so they reuse the same total limits. That is an
    assumption, and it is the reason the whole variable component is behind a
    toggle: a user who wants to see only committed cash flows can switch it off.

    **The rate is a property of the month, not of the horizon.** Both the pool and
    its denominator are computed over the whole month and only then clipped to the
    requested window. Dividing a full month's budget across the two or three days
    of it that happen to fall inside a 30-day horizon would invent a spending
    cliff, and — worse — the projected balance for a given date would change when
    the user switched the horizon to 60 days. A forecast whose near-term shape
    depends on how far out you asked is not a forecast.
    """
    per_day: dict[date, int] = {}
    cursor = date(start.year, start.month, 1)
    last_month = date(end.year, end.month, 1)

    while cursor <= last_month:
        month_start = cursor
        month_end = _month_end(cursor)
        window_start = max(month_start, start)
        window_end = min(month_end, end)
        cursor = budget_service.add_months(cursor, 1)
        if window_start > window_end:
            continue

        report = budget_service.build_report(
            db, user, anchor=month_start, period=BudgetPeriod.MONTHLY.value, today=today
        )
        if month_start <= today <= month_end:
            # Live month: only what is genuinely left to spend, spread over the
            # days of the month that are still ahead.
            pool = sum(max(0, line.remaining_cents) for line in report.lines)
            spread_start = max(month_start, today)
        else:
            # Future month: the limits themselves, since nothing has been spent.
            pool = sum(max(0, line.limit_cents) for line in report.lines)
            spread_start = month_start

        # Net out the modelled bills that land in this same month — see the
        # module docstring. Without this, every budgeted subscription is
        # subtracted twice. Scanned over the whole month for the same reason the
        # denominator is: a bill sitting just past the horizon still consumes
        # budget, and ignoring it would overstate the pool for a clipped month.
        committed = sum(
            -e.amount_cents
            for e in scheduled
            if e.amount_cents < 0 and month_start <= e.date <= month_end
        )
        pool = max(0, pool - committed)

        days = (month_end - spread_start).days + 1
        if days <= 0:
            continue
        base, remainder = divmod(pool, days)
        for offset in range(days):
            day = spread_start + timedelta(days=offset)
            if not (window_start <= day <= window_end):
                continue
            # The remainder is handed out a cent at a time to the earliest days,
            # so a full month's daily amounts sum back to `pool` exactly.
            per_day[day] = base + (1 if offset < remainder else 0)

    return per_day


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #

@dataclass
class ForecastDay:
    date: date
    opening_cents: int
    inflow_cents: int
    recurring_outflow_cents: int
    variable_outflow_cents: int
    closing_cents: int
    below_threshold: bool
    events: list[ForecastEvent] = field(default_factory=list)


@dataclass
class LowCashPoint:
    date: date
    balance_cents: int
    days_away: int
    shortfall_cents: int


@dataclass
class Forecast:
    start_date: date
    end_date: date
    horizon_days: int
    starting_cash_cents: int
    ending_cash_cents: int
    month_end_cents: int
    min_balance_cents: int
    min_balance_date: date
    threshold_cents: int
    include_variable: bool
    total_inflow_cents: int
    total_recurring_outflow_cents: int
    total_variable_outflow_cents: int
    accounts: list[dict] = field(default_factory=list)
    days: list[ForecastDay] = field(default_factory=list)
    low_points: list[LowCashPoint] = field(default_factory=list)


def build_forecast(
    db: Session,
    user: User,
    horizon_days: int = 30,
    threshold_cents: int = DEFAULT_MIN_BALANCE_CENTS,
    include_variable: bool = True,
    today: date | None = None,
) -> Forecast:
    today = today or date.today()
    start = today
    end = today + timedelta(days=horizon_days)

    starting_cash, accounts = current_cash_cents(db, user)
    events = scheduled_events(db, user, start, end)

    # The variable model reasons in whole calendar months, so it needs the bills
    # for the whole of the final month — including any that land after the
    # horizon. Projected separately from `events`, which stays clipped to the
    # horizon because those are the only ones the user is shown.
    variable = {}
    if include_variable:
        month_events = scheduled_events(db, user, start, _month_end(end))
        variable = variable_daily_cents(db, user, start, end, month_events, today)

    by_day: dict[date, list[ForecastEvent]] = {}
    for event in events:
        by_day.setdefault(event.date, []).append(event)

    forecast = Forecast(
        start_date=start,
        end_date=end,
        horizon_days=horizon_days,
        starting_cash_cents=starting_cash,
        ending_cash_cents=starting_cash,
        month_end_cents=starting_cash,
        min_balance_cents=starting_cash,
        min_balance_date=start,
        threshold_cents=threshold_cents,
        include_variable=include_variable,
        total_inflow_cents=0,
        total_recurring_outflow_cents=0,
        total_variable_outflow_cents=0,
        accounts=accounts,
    )

    month_end = _month_end(today)
    balance = starting_cash

    for offset in range(horizon_days + 1):
        day = start + timedelta(days=offset)
        opening = balance
        day_events = by_day.get(day, [])

        inflow = sum(e.amount_cents for e in day_events if e.amount_cents > 0)
        recurring_out = -sum(e.amount_cents for e in day_events if e.amount_cents < 0)
        # Day 0 is today, already lived through: its discretionary spending is
        # in the ledger, not in the future. Charging it again would start the
        # whole curve one day's burn too low.
        variable_out = variable.get(day, 0) if offset > 0 else 0

        balance = opening + inflow - recurring_out - variable_out

        below = balance < threshold_cents
        forecast.days.append(
            ForecastDay(
                date=day,
                opening_cents=opening,
                inflow_cents=inflow,
                recurring_outflow_cents=recurring_out,
                variable_outflow_cents=variable_out,
                closing_cents=balance,
                below_threshold=below,
                events=day_events,
            )
        )

        forecast.total_inflow_cents += inflow
        forecast.total_recurring_outflow_cents += recurring_out
        forecast.total_variable_outflow_cents += variable_out

        if balance < forecast.min_balance_cents:
            forecast.min_balance_cents = balance
            forecast.min_balance_date = day
        if day == month_end:
            forecast.month_end_cents = balance

    forecast.ending_cash_cents = balance
    forecast.low_points = _low_points(forecast.days, threshold_cents, start)
    return forecast


def _low_points(
    days: list[ForecastDay], threshold_cents: int, start: date
) -> list[LowCashPoint]:
    """One entry per contiguous run below the threshold, at its worst day.

    A dip that lasts nine days is one problem, not nine. Emitting a warning per
    day would bury the single fact the user needs — when it starts and how deep it
    gets — under a wall of near-identical rows.
    """
    points: list[LowCashPoint] = []
    run: list[ForecastDay] = []

    for day in [*days, None]:
        if day is not None and day.below_threshold:
            run.append(day)
            continue
        if run:
            worst = min(run, key=lambda d: d.closing_cents)
            points.append(
                LowCashPoint(
                    date=worst.date,
                    balance_cents=worst.closing_cents,
                    days_away=(worst.date - start).days,
                    shortfall_cents=threshold_cents - worst.closing_cents,
                )
            )
            run = []

    return points
