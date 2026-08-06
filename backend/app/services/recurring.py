"""Recurring transaction & subscription detection.

The job is to look at a pile of history and answer "which of these will happen
again, when, and for how much" — because that is the only input a forward-looking
cash flow forecast can be built on.

**Two independent gates, both required.** A candidate group must pass a *cadence*
test and an *amount* test. Either alone produces nonsense:

* Cadence alone would promote a daily coffee habit to a weekly subscription. Twelve
  Blue Bottle visits a month have a median gap of ~2 days, which sits inside a
  naive "7 ± 4" window.
* Amount alone would promote every $6.75 coffee to a stream because the price is
  stable. Stability of price is not recurrence.

So both must hold, and both are measured as an *agreement ratio* rather than on
the median alone. A stream where 8 of 9 gaps are ~30 days and one is 61 (a missed
month) is still monthly; a stream where the gaps are 3, 40, 5, 55 is not, even
though its median might land near 30.

**Why the median, not the mean.** One holiday-season double charge or one late
payment should not move the baseline. `expected_amount_cents` is the median of
the observations and is what the forecaster spends; `last_amount_cents` is the
most recent charge, and the gap between the two is precisely the price-hike
signal (see `subscription_metrics`).

**What is excluded from candidacy.** Detected internal transfers are skipped
outright. A credit-card payment is perfectly regular and perfectly large, and
including it would both double count in the forecast (the card spend is already
counted where it was swiped) and dominate every subscription metric with a number
that is not a commitment at all.

**Idempotence and the human override.** Re-running detection converges: streams
are upserted on `(user, normalized_key, frequency, direction)`. A status the user
set by hand is never overwritten — a cancelled subscription stays cancelled even
if history still shows a perfect cadence, mirroring the `CategorySource.USER`
invariant Phase 2 established for categories.
"""

import calendar
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Category,
    Item,
    Merchant,
    RecurringStream,
    Transaction,
    User,
)
from app.models.enums import (
    FREQUENCY_DAYS,
    RecurrenceFrequency,
    StatusSource,
    StreamStatus,
    TransactionDirection,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Tuning
# --------------------------------------------------------------------------- #

# Two occurrences are a coincidence; three are a pattern. Below this a "stream"
# is a guess, and a guess that lands in a cash flow forecast is worse than a gap.
MIN_OCCURRENCES = 3

# Spec tolerance on the gap between occurrences.
INTERVAL_TOLERANCE_DAYS = 4

# Spec tolerance on amount: exact, or within ±5% for a variable bill.
AMOUNT_TOLERANCE_BPS = 500

# Fraction of observations that must agree, for both gates. Set below 1.0 so one
# missed month or one prorated charge does not disqualify an obvious stream, and
# well above 0.5 so a bimodal group cannot sneak through on its better half.
MIN_AGREEMENT_RATIO = 0.7

# How far back detection looks. Three years, not two: three ANNUALLY occurrences
# span 730 days edge to edge, so a two-year window admits them only if the first
# charge lands exactly on the boundary. One day of drift — a weekend, a card
# reissue — and an annual plan silently stops being detectable. The extra year is
# slack, not history we care about for its own sake.
DEFAULT_LOOKBACK_DAYS = 1095

# A stream whose next charge is this far overdue is "forgotten/unused" — the
# spec's expected window plus a grace period for weekend/holiday settlement.
MISSED_GRACE_DAYS = 14

# A charge above baseline by more than this is a price hike rather than noise.
# Deliberately wider than AMOUNT_TOLERANCE_BPS: a move inside the tolerance band
# is what "variable bill" means, and flagging it would cry wolf every month.
PRICE_HIKE_BPS = 500

# Categories whose parent makes the subscription question answerable outright,
# so the amount-stability heuristic below is never consulted for them.
SUBSCRIPTION_PARENT_SLUGS = {"subscriptions", "entertainment"}
BILL_PARENT_SLUGS = {"housing", "utilities", "financial", "health", "transportation"}

# Leaves that outrank their parent. Rolling up to the parent is right almost
# everywhere, but a few children sit under a "bill" parent while being exactly
# the thing the /subscriptions view exists to surface. A gym membership rolls up
# to `health` next to prescriptions and copays; it is nonetheless the most
# cancelled subscription there is, and filing it under "bills you cannot avoid"
# would be the most obviously wrong row on the page.
SUBSCRIPTION_CHILD_SLUGS = {
    "health.fitness_and_gym",
    "transportation.public_transit",  # a monthly pass is a plan; a fare is not
}

# Amount stability at which an unclassified stream is called a subscription.
# 1% — effectively "the price is a price, not a meter reading".
SUBSCRIPTION_VARIANCE_BPS = 100


# --------------------------------------------------------------------------- #
# Calendar arithmetic
# --------------------------------------------------------------------------- #

def add_months(anchor: date, months: int) -> date:
    """Add months preserving day-of-month, clamped to the target month's length.

    Jan 31 + 1 month is Feb 28, and — importantly — the *next* hop from that
    clamped date is Mar 28, not Mar 31. Clamping is lossy. It is still the right
    trade here: the alternative is carrying the original anchor day forever,
    which for a next-expected-date that the user can edit is more surprising than
    a one-day drift on the handful of merchants that bill on the 31st.
    """
    total = anchor.year * 12 + (anchor.month - 1) + months
    year, month = total // 12, total % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def advance(anchor: date, frequency: str, periods: int = 1) -> date:
    """One (or `periods`) cadence hops forward from `anchor`.

    Day arithmetic for the sub-monthly frequencies — a biweekly paycheck really
    is every 14 days and lands on the same weekday. Calendar arithmetic for the
    rest, so a bill due on the 15th stays on the 15th instead of walking two days
    earlier every hop the way a flat +30 would.
    """
    if frequency == RecurrenceFrequency.WEEKLY.value:
        return anchor + timedelta(days=7 * periods)
    if frequency == RecurrenceFrequency.BIWEEKLY.value:
        return anchor + timedelta(days=14 * periods)
    if frequency == RecurrenceFrequency.QUARTERLY.value:
        return add_months(anchor, 3 * periods)
    if frequency == RecurrenceFrequency.ANNUALLY.value:
        return add_months(anchor, 12 * periods)
    return add_months(anchor, periods)


def occurrences_between(
    stream_next: date, frequency: str, start: date, end: date, limit: int = 400
) -> list[date]:
    """Dates a stream is expected to fire on within [start, end], inclusive.

    An overdue stream is rolled forward to its first non-past occurrence rather
    than being fired on `start`. A bill three weeks late was either paid without
    being linked or has quietly stopped; either way, dropping the whole
    outstanding balance on tomorrow would invent a cliff that never happens.
    """
    cursor = stream_next
    hops = 0
    while cursor < start and hops < limit:
        cursor = advance(cursor, frequency)
        hops += 1

    dates: list[date] = []
    while cursor <= end and len(dates) < limit:
        dates.append(cursor)
        cursor = advance(cursor, frequency)
    return dates


# --------------------------------------------------------------------------- #
# Statistics (integer-only — no float ever touches money)
# --------------------------------------------------------------------------- #

def _median(values: list[int]) -> int:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def _variance_bps(amounts: list[int], baseline: int) -> int:
    """Median absolute deviation from the baseline, in basis points of it.

    Median rather than max so a single outlier charge does not label a fixed-price
    subscription "variable" and disqualify it from the subscription heuristic.
    """
    if baseline <= 0:
        return 0
    return _median([abs(a - baseline) * 10_000 // baseline for a in amounts])


# --------------------------------------------------------------------------- #
# Cadence & amount fitting
# --------------------------------------------------------------------------- #

@dataclass
class Fit:
    frequency: str
    median_interval_days: int
    interval_agreement: int  # percent
    amount_agreement: int  # percent
    expected_amount_cents: int
    amount_variance_bps: int


def _fit_frequency(intervals: list[int]) -> tuple[str, int] | None:
    """Best-fitting frequency for a list of observed gaps, with its agreement.

    Every frequency is scored by the share of gaps landing inside its ±4-day
    window, and the best is taken. Scoring all of them rather than bucketing the
    median matters where the windows abut: WEEKLY spans 3–11 days and BIWEEKLY
    10–18, so a stream with a median of 10 is genuinely ambiguous and should be
    settled by how the *whole* series behaves, not by the middle element.
    """
    best: tuple[str, int] | None = None
    for frequency, nominal in FREQUENCY_DAYS.items():
        hits = sum(1 for gap in intervals if abs(gap - nominal) <= _tolerance(nominal))
        agreement = hits * 100 // len(intervals)
        if best is None or agreement > best[1]:
            best = (frequency, agreement)
    return best


def _tolerance(nominal_days: int) -> int:
    """Allowed slack around a nominal interval.

    The spec's ±4 days, except that month-length variation alone spans 28–31, so
    a strict ±4 around 30 would reject a February-to-March gap of 31 days paired
    with a January gap of 28 for no reason a user would recognise. Longer
    cadences get proportionally more slack for the same reason — a quarter is
    89–92 days depending where in the year it falls.
    """
    if nominal_days >= FREQUENCY_DAYS[RecurrenceFrequency.ANNUALLY.value]:
        return 16
    if nominal_days >= FREQUENCY_DAYS[RecurrenceFrequency.QUARTERLY.value]:
        return 8
    if nominal_days >= FREQUENCY_DAYS[RecurrenceFrequency.MONTHLY.value]:
        return INTERVAL_TOLERANCE_DAYS + 2
    return INTERVAL_TOLERANCE_DAYS


def fit_stream(dates: list[date], amounts: list[int]) -> Fit | None:
    """Classify one merchant/direction group, or reject it.

    `amounts` are positive magnitudes, ordered with `dates`.
    """
    if len(dates) < MIN_OCCURRENCES:
        return None

    intervals = [(b - a).days for a, b in zip(dates, dates[1:])]
    # Same-day repeats are two purchases, not a cadence of zero. They are dropped
    # from the interval series rather than the group: three lunches at one café in
    # one week should not manufacture a "daily" stream, but they also should not
    # stop a genuine monthly charge at the same merchant from being seen.
    intervals = [gap for gap in intervals if gap > 0]
    if len(intervals) < MIN_OCCURRENCES - 1:
        return None

    fitted = _fit_frequency(intervals)
    if fitted is None:
        return None
    frequency, interval_agreement = fitted
    if interval_agreement < MIN_AGREEMENT_RATIO * 100:
        return None

    baseline = _median(amounts)
    if baseline <= 0:
        return None
    within = sum(
        1 for a in amounts if abs(a - baseline) * 10_000 <= baseline * AMOUNT_TOLERANCE_BPS
    )
    amount_agreement = within * 100 // len(amounts)
    if amount_agreement < MIN_AGREEMENT_RATIO * 100:
        return None

    return Fit(
        frequency=frequency,
        median_interval_days=_median(intervals),
        interval_agreement=interval_agreement,
        amount_agreement=amount_agreement,
        expected_amount_cents=baseline,
        amount_variance_bps=_variance_bps(amounts, baseline),
    )


# --------------------------------------------------------------------------- #
# Subscription classification
# --------------------------------------------------------------------------- #

def classify_subscription(
    fit: Fit, direction: str, slug: str | None, parent_slug: str | None
) -> bool:
    """Is this stream a *subscription* rather than a bill or a paycheck?

    The line between "subscription" and "bill" is genuinely fuzzy — both are
    recurring commitments — so category is consulted first and the answer is only
    inferred when the taxonomy is silent. Rent and electricity are excluded not
    because they are less committing but because the /subscriptions view exists to
    answer "what am I paying for that I could cancel", and rent is not that.

    Weekly cadences are excluded: a weekly charge is a habit (groceries, commute),
    not a plan renewal.
    """
    if direction != TransactionDirection.OUTFLOW.value:
        return False
    if fit.frequency in (
        RecurrenceFrequency.WEEKLY.value,
        RecurrenceFrequency.BIWEEKLY.value,
    ):
        return False

    # Most specific wins: a leaf override beats its parent's verdict.
    if slug in SUBSCRIPTION_CHILD_SLUGS:
        return True
    if parent_slug in SUBSCRIPTION_PARENT_SLUGS:
        return True
    if parent_slug in BILL_PARENT_SLUGS:
        return False

    # Taxonomy is silent (or the stream is uncategorised): fall back to price
    # behaviour. A charge that is identical every period is a plan; one that moves
    # with usage is a bill.
    return fit.amount_variance_bps <= SUBSCRIPTION_VARIANCE_BPS


# --------------------------------------------------------------------------- #
# Detection pass
# --------------------------------------------------------------------------- #

@dataclass
class DetectionResult:
    scanned: int = 0
    groups_considered: int = 0
    streams_created: int = 0
    streams_updated: int = 0
    streams_unchanged: int = 0
    transactions_linked: int = 0
    stale_marked: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _candidate_transactions(
    db: Session, user: User, start: date, end: date
) -> list[Transaction]:
    return list(
        db.scalars(
            select(Transaction)
            .join(Account, Transaction.account_id == Account.id)
            .join(Item, Account.item_id == Item.id)
            .where(
                Item.user_id == user.id,
                Transaction.date >= start,
                Transaction.date <= end,
                Transaction.normalized_key.is_not(None),
                Transaction.normalized_key != "",
                Transaction.amount_cents != 0,
                # Pending rows are provisional: the amount and date can both move
                # when they post, which would poison a cadence built on them.
                Transaction.is_pending.is_(False),
                # See the module docstring — a paired card payment is regular,
                # large, and not a commitment.
                Transaction.is_transfer.is_(False),
            )
            .order_by(Transaction.date.asc(), Transaction.id.asc())
        ).all()
    )


def _slug_pairs(db: Session, user: User) -> dict[uuid.UUID, tuple[str, str]]:
    """Category id -> (own slug, parent slug).

    Both are needed: classification consults the leaf first for the handful of
    children that outrank their parent, then falls back to the parent.
    """
    categories = db.scalars(
        select(Category).where((Category.user_id.is_(None)) | (Category.user_id == user.id))
    ).all()
    by_id = {c.id: c for c in categories}
    resolved: dict[uuid.UUID, tuple[str, str]] = {}
    for category in categories:
        parent = by_id.get(category.parent_id) if category.parent_id else category
        resolved[category.id] = (category.slug, (parent or category).slug)
    return resolved


def _dominant(values: list) -> object | None:
    """Most common non-null value — used to pick a stream's category/merchant."""
    counts: dict[object, int] = {}
    for value in values:
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def detect_recurring(
    db: Session,
    user: User,
    start_date: date | None = None,
    end_date: date | None = None,
    today: date | None = None,
    commit: bool = True,
) -> DetectionResult:
    today = today or date.today()
    end_date = end_date or today
    start_date = start_date or end_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)

    rows = _candidate_transactions(db, user, start_date, end_date)
    result = DetectionResult(scanned=len(rows))

    # Group by (merchant identity, direction). Refunds and charges at the same
    # merchant are different streams, not noise in one.
    groups: dict[tuple[str, str], list[Transaction]] = {}
    for txn in rows:
        key = (txn.normalized_key, txn.direction)
        groups.setdefault(key, []).append(txn)

    slug_of = _slug_pairs(db, user)
    existing = {
        (s.normalized_key, s.frequency, s.direction): s
        for s in db.scalars(
            select(RecurringStream).where(RecurringStream.user_id == user.id)
        ).all()
    }
    seen: set[uuid.UUID] = set()

    for (normalized_key, direction), members in groups.items():
        if len(members) < MIN_OCCURRENCES:
            continue
        result.groups_considered += 1

        dates = [t.date for t in members]
        amounts = [abs(t.amount_cents) for t in members]
        fit = fit_stream(dates, amounts)
        if fit is None:
            continue

        category_id = _dominant([t.category_id for t in members])
        merchant_id = _dominant([t.merchant_id for t in members])
        slug, parent_slug = (
            slug_of.get(category_id, (None, None)) if category_id else (None, None)
        )
        display_name = _display_name(db, merchant_id, members)

        stream = existing.get((normalized_key, fit.frequency, direction))
        created = stream is None
        if created:
            stream = RecurringStream(
                user_id=user.id,
                normalized_key=normalized_key,
                frequency=fit.frequency,
                direction=direction,
                status=StreamStatus.ACTIVE.value,
                status_source=StatusSource.AUTO.value,
            )
            db.add(stream)

        before = (
            stream.expected_amount_cents,
            stream.last_date,
            stream.occurrence_count,
        ) if not created else None

        stream.merchant_id = merchant_id
        stream.category_id = category_id
        stream.display_name = display_name
        stream.expected_amount_cents = fit.expected_amount_cents
        stream.last_amount_cents = amounts[-1]
        stream.amount_variance_bps = fit.amount_variance_bps
        stream.first_date = dates[0]
        stream.last_date = dates[-1]
        stream.next_expected_date = advance(dates[-1], fit.frequency)
        stream.median_interval_days = fit.median_interval_days
        stream.occurrence_count = len(members)

        if not stream.is_subscription_locked:
            stream.is_subscription = classify_subscription(fit, direction, slug, parent_slug)
        # A status the user set by hand is final. Detection may only revive a
        # stream it paused itself.
        if stream.status_source != StatusSource.USER.value:
            stream.status = _auto_status(stream, today)

        db.flush()
        seen.add(stream.id)

        for txn in members:
            if txn.recurring_stream_id != stream.id:
                txn.recurring_stream_id = stream.id
                result.transactions_linked += 1
            txn.is_recurring = True

        if created:
            result.streams_created += 1
        elif before != (
            stream.expected_amount_cents,
            stream.last_date,
            stream.occurrence_count,
        ):
            result.streams_updated += 1
        else:
            result.streams_unchanged += 1

    result.stale_marked = _retire_unseen(db, user, existing.values(), seen, today)

    if commit:
        db.commit()
    return result


def _display_name(db: Session, merchant_id: uuid.UUID | None, members: list[Transaction]) -> str:
    if merchant_id is not None:
        merchant = db.get(Merchant, merchant_id)
        if merchant is not None:
            return merchant.display_name
    latest = members[-1]
    return latest.merchant_name or latest.description_raw[:255]


def _auto_status(stream: RecurringStream, today: date) -> str:
    """Status inferred from how overdue the stream is.

    Two missed cycles is the cancellation threshold rather than one, because one
    missed cycle is routinely a settlement delay or a card reissue. PAUSED, the
    intermediate state, is what the /subscriptions view surfaces as "forgotten?"
    — the point at which it is worth asking the user, not yet worth deciding for
    them.
    """
    overdue_days = (today - stream.next_expected_date).days
    if overdue_days <= MISSED_GRACE_DAYS:
        return StreamStatus.ACTIVE.value

    cycle = FREQUENCY_DAYS[stream.frequency]
    if overdue_days > 2 * cycle + MISSED_GRACE_DAYS:
        return StreamStatus.CANCELLED.value
    return StreamStatus.PAUSED.value


def _retire_unseen(
    db: Session,
    user: User,
    all_streams,
    seen: set[uuid.UUID],
    today: date,
) -> int:
    """Re-evaluate streams whose group no longer fits.

    A stream can vanish from the detection output because its charges stopped —
    which is exactly the "cancelled subscription" signal and must not be silently
    dropped on the floor. The row is kept (its history is still true) and its
    status is re-derived from how overdue it now is.
    """
    retired = 0
    for stream in all_streams:
        if stream.id in seen or stream.status_source == StatusSource.USER.value:
            continue
        new_status = _auto_status(stream, today)
        if new_status != stream.status:
            stream.status = new_status
            retired += 1
    return retired


# --------------------------------------------------------------------------- #
# Auto-linking (§1.3) — called from ingestion for each incoming transaction
# --------------------------------------------------------------------------- #

def link_transaction(db: Session, user_id: uuid.UUID, txn: Transaction) -> bool:
    """Attach a freshly ingested transaction to its stream, if one exists.

    Runs per row inside the sync loop, so it does exactly one indexed lookup and
    no statistics: full re-fitting is the periodic `detect_recurring` pass's job.
    What this does is keep `last_date` / `next_expected_date` honest between those
    passes, which is what the forecast reads.

    Idempotent under re-sync: the stream's cursor only ever moves forward, so
    replaying a delta cannot push `next_expected_date` an extra cycle into the
    future.
    """
    if not txn.normalized_key or txn.is_pending or txn.is_transfer or txn.amount_cents == 0:
        return False

    # One merchant may carry several cadences (a monthly plan and an annual
    # add-on). Prefer the best-evidenced one; ordering also makes the choice
    # deterministic instead of whatever the planner returns first.
    stream = db.scalar(
        select(RecurringStream)
        .where(
            RecurringStream.user_id == user_id,
            RecurringStream.normalized_key == txn.normalized_key,
            RecurringStream.direction == txn.direction,
            RecurringStream.status != StreamStatus.CANCELLED.value,
        )
        .order_by(RecurringStream.occurrence_count.desc(), RecurringStream.id.asc())
        .limit(1)
    )
    if stream is None:
        return False

    amount = abs(txn.amount_cents)
    # Guard against a charge that has nothing to do with the stream — a $900 one-off
    # at a merchant whose subscription is $9.99 is not this month's renewal.
    if abs(amount - stream.expected_amount_cents) * 10_000 > stream.expected_amount_cents * (
        AMOUNT_TOLERANCE_BPS + PRICE_HIKE_BPS
    ):
        return False

    txn.recurring_stream_id = stream.id
    txn.is_recurring = True

    if txn.date > stream.last_date:
        stream.last_date = txn.date
        stream.last_amount_cents = amount
        stream.next_expected_date = advance(txn.date, stream.frequency)
        stream.occurrence_count += 1
        # A stream that paid again is alive, unless the user says otherwise.
        if stream.status_source != StatusSource.USER.value:
            stream.status = StreamStatus.ACTIVE.value
    return True
