"""Synthetic ledger for exercising Phases 2 and 3 without a Plaid connection.

This exists because the categorisation, transfer-pairing, pacing, recurrence and
forecasting code paths cannot be trusted until they have run over data that looks
like a real ledger: messy descriptors, a credit-card payment that must not count
as spending, a refund that must net out, and enough history for the trailing
median suggester and the recurrence fitter to have something to chew on.

Everything it writes is namespaced under the `DEMO_` provider ids, so
`python -m app.seeds.demo --clear` removes it completely and leaves real
Plaid-linked data untouched.

    python -m app.seeds.demo          # create (idempotent)
    python -m app.seeds.demo --clear  # remove

**Why the ledger is split in two.** Phase 3 detection passes a stream only when
*both* the cadence gate and the amount gate agree, so a seed that jitters every
amount and scatters every date — as this file originally did — produces exactly
zero streams and makes /subscriptions and /forecast look broken. The two lists
below are therefore adversarial by construction:

* `RECURRING` is regular on purpose. Fixed day, fixed amount, real frequencies.
  Every entry here *must* be detected, and the detector is wrong if it misses one.
* `DISCRETIONARY` is irregular on purpose. Twelve coffees a month have a median
  gap of ~2 days and a stable price; four grocery runs have a median gap of ~7.
  Both sit inside a naive cadence window, and neither is a commitment. Nothing
  here may be detected, and the detector is wrong if it promotes one.

The amount jitter on `DISCRETIONARY` is what does the rejecting: uniform over
±(15–20)% puts only ~29% of observations inside the ±5% amount band, well under
the 70% agreement the second gate demands.

The `RECURRING` table also deliberately encodes the three states the subscription
dashboard has to render, so none of them requires hand-editing a row afterwards:
a live price hike, an auto-cancelled plan, and a recently-missed one.
"""

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import Account, Institution, Item, RecurringStream, Transaction
from app.models.enums import RecurrenceFrequency
from app.services.categorization import CategoryResolver, apply_to_transaction
from app.services.normalization import resolve_merchant
from app.services.recurring import DEFAULT_LOOKBACK_DAYS, detect_recurring
from app.services.transfers import detect_transfers
from app.services.users import get_current_user

DEMO_ITEM_ID = "DEMO_ITEM"
DEMO_CHECKING = "DEMO_CHECKING"
DEMO_CARD = "DEMO_CARD"

MONTHLY = RecurrenceFrequency.MONTHLY.value
QUARTERLY = RecurrenceFrequency.QUARTERLY.value
ANNUALLY = RecurrenceFrequency.ANNUALLY.value
BIWEEKLY = RecurrenceFrequency.BIWEEKLY.value


@dataclass(frozen=True)
class Stream:
    """One intentionally-regular series.

    `day` is the day of month for MONTHLY. The other frequencies are anchored to
    explicit day offsets from today (`offsets`) instead, because a quarterly or
    annual series pinned to a calendar month would drift in and out of the
    detection lookback depending on what today happens to be — and a seed whose
    output depends on the date you run it is not a fixture.
    """

    descriptor: str
    category: str
    dollars: float
    account: str  # "card" | "checking"
    frequency: str
    day: int | None = None
    offsets: tuple[int, ...] = ()
    # Stop generating this many months back — an ended subscription.
    ends_months_back: int = 0
    # (new price, months back it took effect) — a mid-history price hike.
    price_change: tuple[float, int] | None = None
    # Cycling multipliers for a metered bill. Kept inside ±5% so the amount gate
    # still passes: this is the spec's "variable bill", not noise.
    variance: tuple[float, ...] = ()


MONTHS_OF_HISTORY = 26

RECURRING = [
    # --- income ---------------------------------------------------------- #
    # Genuinely every 14 days, not "twice a month". Semi-monthly payroll (the 1st
    # and the 15th) alternates 14/17-day gaps, which fits BIWEEKLY only by luck of
    # the tolerance; a real biweekly anchor is the honest fixture.
    Stream(
        "ACH PAYROLL DIRECT DEP ACME CORP", "INCOME_WAGES", 2600.00, "checking",
        BIWEEKLY, offsets=tuple(range(3, 730, 14)),
    ),
    # --- bills (not cancellable, excluded from the subscription totals) ---- #
    Stream(
        "RENT PAYMENT SUNSET PROPERTIES", "RENT_AND_UTILITIES_RENT", -2450.00,
        "checking", MONTHLY, day=1,
    ),
    Stream(
        "PG&E WEB ONLINE PAYMENT", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
        -118.40, "checking", MONTHLY, day=20,
        variance=(1.00, 0.97, 1.04, 0.96, 1.02, 0.98, 1.03, 0.99),
    ),
    Stream(
        "COMCAST CABLE COMM", "RENT_AND_UTILITIES_INTERNET_AND_CABLE", -89.99,
        "checking", MONTHLY, day=24,
    ),
    # Quarterly, landing ~2 weeks out so the 30-day forecast has real lumpiness.
    Stream(
        "STATE FARM INSURANCE PREM", "GENERAL_SERVICES_INSURANCE", -342.00,
        "checking", QUARTERLY, offsets=(350, 259, 168, 77),
    ),
    # --- subscriptions ---------------------------------------------------- #
    # The price hike: $22.99 -> $24.99 four months ago. Four hiked observations
    # against twenty-two at the old price keeps the median (and so the baseline)
    # at 22.99, which is exactly what makes the +8.7% delta legible as a hike.
    Stream(
        "NETFLIX.COM", "ENTERTAINMENT_TV_AND_MOVIES", -22.99, "card", MONTHLY,
        day=7, price_change=(-24.99, 4),
    ),
    Stream(
        "SPOTIFY USA", "ENTERTAINMENT_MUSIC_AND_AUDIO", -11.99, "card",
        MONTHLY, day=12,
    ),
    # Rolls up to `health`, a bill parent, and is rescued by the leaf override in
    # services/recurring.SUBSCRIPTION_CHILD_SLUGS.
    Stream(
        "EQUINOX SF", "PERSONAL_CARE_GYMS_AND_FITNESS_CENTERS", -215.00, "card",
        MONTHLY, day=3,
    ),
    # Annual. Three occurrences span 730 days, which is why the detector's
    # default lookback is three years rather than two.
    Stream(
        "DROPBOX*ANNUAL PLAN", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES", -119.88,
        "card", ANNUALLY, offsets=(740, 375, 10),
    ),
    # Stopped 6 months ago: >2 missed monthly cycles, so detection auto-cancels
    # it. Shows up under "forgotten" because the engine noticed, not the user.
    Stream(
        "ADOBE CREATIVE CLOUD", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES", -54.99,
        "card", MONTHLY, day=18, ends_months_back=6,
    ),
    # Stopped ~7 weeks ago: one missed cycle, so it lands in PAUSED — the "did you
    # mean to cancel this?" state, still counted as a live commitment.
    Stream(
        "NYTIMES DIGITAL SUBSCRIPTION", "GENERAL_SERVICES_OTHER_GENERAL_SERVICES",
        -17.00, "card", MONTHLY, day=22, ends_months_back=3,
    ),
]

# (descriptor, plaid detailed category, dollars, times per month). Jittered and
# scattered on purpose — see the module docstring. None of this may be detected.
DISCRETIONARY = [
    ("SQ *BLUE BOTTLE COFFEE 0421 OAKLAND CA", "FOOD_AND_DRINK_COFFEE", 6.75, 12),
    ("TST* SIDECAR DONUTS", "FOOD_AND_DRINK_RESTAURANT", 14.20, 4),
    ("WHOLE FOODS MKT 102", "FOOD_AND_DRINK_GROCERIES", 96.40, 4),
    ("TRADER JOE'S #178", "FOOD_AND_DRINK_GROCERIES", 62.15, 2),
    ("SHELL OIL 12345678 SAN JOSE CA", "TRANSPORTATION_GAS", 48.90, 3),
    ("UBER *TRIP HELP.UBER.COM", "TRANSPORTATION_TAXIS_AND_RIDE_SHARES", 22.35, 5),
    ("AMZN MKTP US*2X4YT8LM3", "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES", 41.99, 6),
    ("WALGREENS #4471", "MEDICAL_PHARMACIES_AND_SUPPLEMENTS", 28.60, 2),
]

# Discretionary noise only needs to cover the window the budget views read. Two
# years of coffee would quadruple the seed for no extra signal.
DISCRETIONARY_MONTHS = 6


def _month_start(anchor: date, months_back: int) -> date:
    year, month = anchor.year, anchor.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _clear(db) -> int:
    user = get_current_user(db)
    item = db.scalar(
        select(Item).where(Item.user_id == user.id, Item.provider_item_id == DEMO_ITEM_ID)
    )
    if item is None:
        return 0

    account_ids = list(
        db.scalars(select(Account.id).where(Account.item_id == item.id)).all()
    )
    removed = 0
    if account_ids:
        # Break the self-referential transfer links first; a plain DELETE would
        # trip the FK from the surviving leg of a pair.
        db.query(Transaction).filter(Transaction.account_id.in_(account_ids)).update(
            {Transaction.transfer_pair_id: None}, synchronize_session=False
        )
        removed = db.execute(
            delete(Transaction).where(Transaction.account_id.in_(account_ids))
        ).rowcount
        db.execute(delete(Account).where(Account.item_id == item.id))

    # Recurring streams are user-scoped, not item-scoped, so there is no way to
    # delete "only the demo ones". Dropping all of them is safe because they are
    # derived data: the next detection pass rebuilds them from whatever history
    # survives. The transactions that referenced them are already gone above.
    db.execute(delete(RecurringStream).where(RecurringStream.user_id == user.id))

    db.delete(item)
    db.commit()
    return removed


def _accounts(db, user) -> tuple[Account, Account]:
    institution = db.scalar(
        select(Institution).where(Institution.provider_institution_id == "DEMO_INS")
    )
    if institution is None:
        institution = Institution(provider_institution_id="DEMO_INS", name="Demo Bank")
        db.add(institution)
        db.flush()

    item = db.scalar(
        select(Item).where(Item.user_id == user.id, Item.provider_item_id == DEMO_ITEM_ID)
    )
    if item is None:
        item = Item(
            user_id=user.id,
            institution_id=institution.id,
            provider_item_id=DEMO_ITEM_ID,
            # Not a real token and never decrypted — this item is never synced.
            access_token="demo-not-a-real-token",
            status="good",
        )
        db.add(item)
        db.flush()

    existing = {
        a.provider_account_id: a
        for a in db.scalars(select(Account).where(Account.item_id == item.id)).all()
    }

    checking = existing.get(DEMO_CHECKING) or Account(
        item_id=item.id,
        provider_account_id=DEMO_CHECKING,
        name="Everyday Checking",
        mask="4412",
        type="depository",
        subtype="checking",
        current_balance_cents=812_44,
        available_balance_cents=812_44,
    )
    card = existing.get(DEMO_CARD) or Account(
        item_id=item.id,
        provider_account_id=DEMO_CARD,
        name="Rewards Card",
        mask="9087",
        type="credit",
        subtype="credit card",
        current_balance_cents=-1_284_10,
        credit_limit_cents=1_000_000,
    )
    db.add_all([checking, card])
    db.flush()
    return checking, card


def _add(db, account, seq, descriptor, provider_category, dollars, on):
    cents = int(round(dollars * 100))
    txn = Transaction(
        account_id=account.id,
        provider_txn_id=f"DEMO-{account.provider_account_id}-{seq}",
        amount_cents=cents,
        direction="INFLOW" if cents > 0 else "OUTFLOW",
        date=on,
        posted_date=on,
        description_raw=descriptor,
        provider_category=provider_category,
    )
    db.add(txn)
    return txn


def _stream_occurrences(spec: Stream, today: date) -> list[tuple[date, float]]:
    """Every (date, dollars) this spec should produce, oldest first."""
    out: list[tuple[date, float]] = []

    if spec.frequency == MONTHLY:
        for months_back in range(MONTHS_OF_HISTORY, spec.ends_months_back - 1, -1):
            on = _month_start(today, months_back) + timedelta(days=spec.day - 1)
            if on > today:
                continue
            dollars = spec.dollars
            if spec.price_change is not None:
                new_price, effective_months_back = spec.price_change
                if months_back < effective_months_back:
                    dollars = new_price
            out.append((on, dollars))
    else:
        for offset in sorted(spec.offsets, reverse=True):
            on = today - timedelta(days=offset)
            if on <= today:
                out.append((on, spec.dollars))

    # Metered bills wobble within the amount tolerance. Applied by position so the
    # ledger is identical on every run.
    if spec.variance:
        out = [
            (on, round(dollars * spec.variance[i % len(spec.variance)], 2))
            for i, (on, dollars) in enumerate(out)
        ]
    return out


def seed(db) -> dict:
    user = get_current_user(db)
    checking, card = _accounts(db, user)

    if db.scalar(
        select(Transaction.id).where(Transaction.account_id.in_([checking.id, card.id]))
    ):
        return {"skipped": True}

    # Fixed seed: the same ledger every run, so a number that looks wrong on the
    # dashboard can actually be chased down.
    rng = random.Random(20260727)
    today = date.today()
    accounts = {"checking": checking, "card": card}
    seq = 0
    created = 0

    # --- the regular half: everything here must be detected ---------------- #
    for spec in RECURRING:
        account = accounts[spec.account]
        for on, dollars in _stream_occurrences(spec, today):
            seq += 1
            _add(db, account, seq, spec.descriptor, spec.category, dollars, on)
            created += 1

    # --- the irregular half: nothing here may be detected ------------------ #
    for months_back in range(DISCRETIONARY_MONTHS, -1, -1):
        start = _month_start(today, months_back)
        # Only generate days that have actually happened.
        last_day = 28 if months_back > 0 else min(today.day, 28)
        if last_day < 1:
            continue

        for descriptor, category, dollars, count in DISCRETIONARY:
            for _ in range(max(1, round(count * last_day / 28))):
                seq += 1
                jitter = rng.uniform(0.85, 1.2)
                _add(
                    db,
                    card,
                    seq,
                    descriptor,
                    category,
                    -round(dollars * jitter, 2),
                    start + timedelta(days=rng.randint(0, last_day - 1)),
                )
                created += 1

        # A refund. It must net against spend, not appear as income.
        #
        # Sporadic on purpose. An earlier version of this seed emitted one every
        # month for exactly $34.99, which is a textbook monthly stream — and the
        # detector duly found it, then the forecaster projected $34.99 of
        # perpetual income from it. Refunds are not an income stream, and a
        # fixture that implies they are teaches the engine the wrong lesson.
        if rng.random() < 0.5:
            seq += 1
            _add(
                db,
                card,
                seq,
                "AMZN MKTP US*RETURN",
                "GENERAL_MERCHANDISE_ONLINE_MARKETPLACES",
                round(rng.uniform(18.0, 62.0), 2),
                start + timedelta(days=rng.randint(0, last_day - 1)),
            )
            created += 1

        # The credit-card payment: two legs, opposite signs, one day apart.
        # If pairing fails, this $900 lands in the budget as fictional spending.
        # It is also perfectly regular, which is precisely why detection excludes
        # paired transfers — otherwise it would be the largest "subscription".
        pay_day = start + timedelta(days=min(21, last_day - 1))
        seq += 1
        _add(db, checking, seq, "ONLINE PAYMENT THANK YOU", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", -900.00, pay_day)
        seq += 1
        _add(db, card, seq, "PAYMENT THANK YOU - WEB", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT", 900.00, pay_day + timedelta(days=1))
        created += 2

    db.flush()

    resolver = CategoryResolver(db, user)
    for txn in db.scalars(
        select(Transaction).where(Transaction.account_id.in_([checking.id, card.id]))
    ).all():
        merchant, key = resolve_merchant(db, txn.description_raw, txn.merchant_name)
        txn.normalized_key = key or None
        txn.merchant_id = merchant.id if merchant else None
        apply_to_transaction(txn, resolver)

    db.commit()

    # Transfers first: detection skips paired legs, so the card payment has to be
    # recognised as a transfer *before* the recurrence fitter sees it.
    transfers = detect_transfers(db, user, start_date=_month_start(today, MONTHS_OF_HISTORY))
    detection = detect_recurring(
        db,
        user,
        start_date=today - timedelta(days=DEFAULT_LOOKBACK_DAYS),
        end_date=today,
        today=today,
    )
    return {
        "created": created,
        "transfer_pairs": transfers.pairs_found,
        "streams": detection.streams_created,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="Remove the demo ledger")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.clear:
            print(f"Removed {_clear(db)} demo transactions.")
            return 0
        result = seed(db)
        if result.get("skipped"):
            print("Demo ledger already present; nothing to do.")
        else:
            print(
                f"Created {result['created']} transactions, "
                f"{result['transfer_pairs']} transfer pairs, "
                f"{result['streams']} recurring streams."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
