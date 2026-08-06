"""Subscription analytics: recurring commitments, price hikes, forgotten plans.

Everything here reads `recurring_stream` and derives; nothing is stored. The
streams table is the single source of truth for "what repeats", and duplicating
its numbers into a second table would only create a way for them to disagree.

**Normalising to a month.** A quarterly $180 charge and a monthly $60 charge are
the same commitment, and comparing them requires putting both on one axis. That
conversion is integer-exact in one direction only:

    annual_cents = amount * periods_per_year        (exact)
    monthly_cents = annual_cents / 12               (rounded)

so annualised figures are computed first and the monthly figure is derived from
them. Doing it the other way — monthly first, then ×12 — compounds the rounding
error twelve times and makes the two headline numbers on the dashboard visibly
inconsistent with each other.

Weekly is 52 periods a year, not 4 a month. A weekly $25 charge costs $1,300 a
year, not $1,200; the naive "×4" understates it by a full month.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RecurringStream, User
from app.models.enums import RecurrenceFrequency, StreamStatus, TransactionDirection
from app.services.recurring import (
    MISSED_GRACE_DAYS,
    PRICE_HIKE_BPS,
    occurrences_between,
)

PERIODS_PER_YEAR = {
    RecurrenceFrequency.WEEKLY.value: 52,
    RecurrenceFrequency.BIWEEKLY.value: 26,
    RecurrenceFrequency.MONTHLY.value: 12,
    RecurrenceFrequency.QUARTERLY.value: 4,
    RecurrenceFrequency.ANNUALLY.value: 1,
}


def annualized_cents(amount_cents: int, frequency: str) -> int:
    return amount_cents * PERIODS_PER_YEAR.get(frequency, 12)


def monthly_cents(amount_cents: int, frequency: str) -> int:
    """Monthly-equivalent cost, derived from the annual figure. See module doc."""
    return annualized_cents(amount_cents, frequency) // 12


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

@dataclass
class PriceChange:
    stream_id: uuid.UUID
    display_name: str
    baseline_cents: int
    current_cents: int
    delta_cents: int
    delta_bps: int
    annual_impact_cents: int
    last_date: date


@dataclass
class ForgottenStream:
    stream_id: uuid.UUID
    display_name: str
    expected_amount_cents: int
    frequency: str
    last_date: date
    next_expected_date: date
    days_overdue: int
    status: str


@dataclass
class SubscriptionMetrics:
    # Every recurring outflow — rent and electricity included. This is the honest
    # answer to "what leaves my account no matter what I do this month".
    recurring_monthly_cents: int = 0
    recurring_annual_cents: int = 0
    # The cancellable subset. This is the number the /subscriptions page leads
    # with, because it is the one the user can act on.
    subscription_monthly_cents: int = 0
    subscription_annual_cents: int = 0
    recurring_income_monthly_cents: int = 0

    active_subscription_count: int = 0
    active_recurring_count: int = 0
    paused_count: int = 0
    cancelled_count: int = 0

    price_hikes: list[PriceChange] = field(default_factory=list)
    forgotten: list[ForgottenStream] = field(default_factory=list)


def _all_streams(db: Session, user: User) -> list[RecurringStream]:
    return list(
        db.scalars(
            select(RecurringStream).where(RecurringStream.user_id == user.id)
        ).all()
    )


def subscription_metrics(
    db: Session, user: User, today: date | None = None
) -> SubscriptionMetrics:
    today = today or date.today()
    metrics = SubscriptionMetrics()

    for stream in _all_streams(db, user):
        if stream.status == StreamStatus.CANCELLED.value:
            metrics.cancelled_count += 1
            # A cancelled stream costs nothing and is not a live commitment, so
            # it contributes to neither total. It stays visible in the list so
            # the user can see what they turned off.
            continue
        if stream.status == StreamStatus.PAUSED.value:
            metrics.paused_count += 1

        annual = annualized_cents(stream.expected_amount_cents, stream.frequency)

        if stream.direction == TransactionDirection.INFLOW.value:
            metrics.recurring_income_monthly_cents += annual // 12
            continue

        # Paused streams are excluded from the running totals but counted above:
        # billing them would overstate committed spend, hiding them would make
        # the count and the total disagree.
        if stream.status != StreamStatus.ACTIVE.value:
            continue

        metrics.recurring_annual_cents += annual
        metrics.active_recurring_count += 1
        if stream.is_subscription:
            metrics.subscription_annual_cents += annual
            metrics.active_subscription_count += 1

        hike = detect_price_hike(stream)
        if hike is not None:
            metrics.price_hikes.append(hike)

    metrics.recurring_monthly_cents = metrics.recurring_annual_cents // 12
    metrics.subscription_monthly_cents = metrics.subscription_annual_cents // 12

    metrics.price_hikes.sort(key=lambda h: -h.annual_impact_cents)
    metrics.forgotten = find_forgotten(db, user, today)
    return metrics


def detect_price_hike(stream: RecurringStream) -> PriceChange | None:
    """Flag a charge that came in above the stream's historic baseline.

    Only increases are reported. A price *drop* is not something the user needs
    to act on, and mixing the two directions into one "price changes" list buries
    the actionable half.

    `annual_impact_cents` is the number that matters: a $3 rise on a monthly plan
    is $36 a year, while the same $3 on an annual plan is $3. Sorting the list by
    the monthly delta would rank those identically.
    """
    baseline = stream.expected_amount_cents
    if baseline <= 0:
        return None

    delta = stream.last_amount_cents - baseline
    if delta <= 0:
        return None

    delta_bps = delta * 10_000 // baseline
    if delta_bps <= PRICE_HIKE_BPS:
        return None

    return PriceChange(
        stream_id=stream.id,
        display_name=stream.display_name,
        baseline_cents=baseline,
        current_cents=stream.last_amount_cents,
        delta_cents=delta,
        delta_bps=delta_bps,
        annual_impact_cents=annualized_cents(delta, stream.frequency),
        last_date=stream.last_date,
    )


def find_forgotten(
    db: Session, user: User, today: date | None = None
) -> list[ForgottenStream]:
    """Streams that should have charged by now and did not.

    The spec's window: expected date plus a 14-day grace period. Two readings are
    possible for a silent stream — it was cancelled, or the charge simply has not
    landed — and the honest thing is to surface it and let the user say which.

    Cancelled streams are included when they were cancelled *automatically*: that
    is the engine reporting "this stopped and I noticed", which is exactly the
    forgotten-subscription case. A stream the user cancelled by hand is not a
    finding; they already know.
    """
    today = today or date.today()
    findings: list[ForgottenStream] = []

    for stream in _all_streams(db, user):
        if stream.direction != TransactionDirection.OUTFLOW.value:
            continue
        if stream.status_source != "AUTO":
            continue

        overdue = (today - stream.next_expected_date).days
        if overdue <= MISSED_GRACE_DAYS:
            continue

        findings.append(
            ForgottenStream(
                stream_id=stream.id,
                display_name=stream.display_name,
                expected_amount_cents=stream.expected_amount_cents,
                frequency=stream.frequency,
                last_date=stream.last_date,
                next_expected_date=stream.next_expected_date,
                days_overdue=overdue,
                status=stream.status,
            )
        )

    findings.sort(key=lambda f: -f.days_overdue)
    return findings


# --------------------------------------------------------------------------- #
# Upcoming renewals
# --------------------------------------------------------------------------- #

@dataclass
class Renewal:
    date: date_type
    stream_id: uuid.UUID
    display_name: str
    amount_cents: int
    frequency: str
    direction: str
    is_subscription: bool
    category_name: str | None
    days_away: int


def upcoming_renewals(
    db: Session, user: User, days: int = 30, today: date | None = None
) -> list[Renewal]:
    """Every expected charge in the next `days`, chronologically.

    A weekly stream legitimately appears four or five times in a 30-day window,
    so this expands each stream across the horizon rather than listing it once.
    Showing one row per stream would understate a weekly commitment by 4x on a
    view whose whole purpose is "what is about to hit my account".
    """
    today = today or date.today()
    horizon = today + timedelta(days=days)

    renewals: list[Renewal] = []
    streams = db.scalars(
        select(RecurringStream).where(
            RecurringStream.user_id == user.id,
            RecurringStream.status == StreamStatus.ACTIVE.value,
        )
    ).all()

    for stream in streams:
        for on in occurrences_between(
            stream.next_expected_date, stream.frequency, today, horizon
        ):
            renewals.append(
                Renewal(
                    date=on,
                    stream_id=stream.id,
                    display_name=stream.display_name,
                    amount_cents=stream.expected_amount_cents,
                    frequency=stream.frequency,
                    direction=stream.direction,
                    is_subscription=stream.is_subscription,
                    category_name=stream.category.name if stream.category else None,
                    days_away=(on - today).days,
                )
            )

    renewals.sort(key=lambda r: (r.date, -r.amount_cents, r.display_name))
    return renewals
