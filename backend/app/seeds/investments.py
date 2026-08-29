"""Synthetic portfolio, liabilities and goals for exercising Phase 4.

Separate from `seeds/demo.py` and deliberately additive: it creates its own
items (`DEMO_INVEST_ITEM`, `DEMO_LOAN_ITEM`) and writes **no rows into
`transaction`**. The Phase 2 categorisation fixtures and the Phase 3 recurrence
and forecast fixtures are tuned to the exact ledger `demo.py` produces, and
dropping a mortgage payment into it would move every budget, every detected
stream and the cash forecast, turning a Phase 4 seed into a Phase 2 regression.

    python -m app.seeds.investments          # create (idempotent)
    python -m app.seeds.investments --clear  # remove

**Liability history comes from `balance_snapshot`, not from the ledger.** A
mortgage balance falls by the *principal* portion of a payment, not the payment,
and that split is not derivable from a bank transaction. Writing the real
declining balance as dated snapshots is both more accurate and what
`net_worth.build_series` already re-anchors on — and it exercises the ACTUAL
branch of the reconstruction walk, which no other fixture reaches.

**Prices are generated, not random.** Each security interpolates geometrically
from a start price to an end price with a deterministic sine wobble, so the
allocation drifts over history the way a real portfolio does (equities outrun
bonds, crypto swings hard) and every run produces byte-identical output. A
fixture whose numbers change between runs cannot be used to chase down a
dashboard number that looks wrong.

Two deliberate holes in the data, because the code paths that handle them are
the ones most likely to be quietly broken:

* the target-date fund in the 401(k) has **no cost basis** — a rolled-over
  position whose basis the custodian never carried across. Gain figures must
  exclude it rather than treat it as 100% profit.
* the money-market sweep is **cash-equivalent**, so it must count toward net
  worth and the CASH allocation slice but stay out of the "invested" return.
"""

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.models import (
    Account,
    BalanceSnapshot,
    FinancialGoal,
    FinancialGoalAccount,
    Holding,
    Institution,
    InvestmentTransaction,
    Item,
    Security,
)
from app.models.enums import GoalCategory, InvestmentTransactionType, SecurityType
from app.services.investment import classify_asset_class
from app.services.users import get_current_user

DEMO_INVEST_ITEM = "DEMO_INVEST_ITEM"
DEMO_LOAN_ITEM = "DEMO_LOAN_ITEM"
# Every security this file mints is namespaced so --clear can find them and a
# real Plaid sync can never collide with them.
SECURITY_PREFIX = "DEMO_SEC_"

MONTHS_OF_HISTORY = 26

EQUITY = SecurityType.EQUITY.value
ETF = SecurityType.ETF.value
MUTUAL_FUND = SecurityType.MUTUAL_FUND.value
CRYPTO = SecurityType.CRYPTO.value
CASH_EQUIVALENT = SecurityType.CASH_EQUIVALENT.value


@dataclass(frozen=True)
class Sec:
    """One instrument and the price path it traced over the history window.

    `wobble` is the amplitude of a deterministic sine ripple around the trend,
    as a fraction. Bonds barely move, crypto moves a lot, and the allocation
    chart is only interesting because they differ.
    """

    ticker: str
    name: str
    type: str
    start_price: float
    end_price: float
    wobble: float
    phase: float

    def price_at(self, index: int, total: int) -> float:
        """Price at month `index` of `total`, oldest = 0."""
        ratio = index / total if total else 1.0
        trend = self.start_price * (self.end_price / self.start_price) ** ratio
        return round(trend * (1 + self.wobble * math.sin(index * 1.7 + self.phase)), 4)


SECURITIES = [
    Sec("VTI", "Vanguard Total Stock Market ETF", ETF, 218.40, 312.75, 0.035, 0.0),
    Sec("VXUS", "Vanguard Total International Stock ETF", ETF, 56.10, 68.40, 0.030, 1.1),
    Sec("BND", "Vanguard Total Bond Market ETF", ETF, 71.20, 74.85, 0.008, 2.3),
    Sec("VNQ", "Vanguard Real Estate ETF", ETF, 82.60, 91.20, 0.028, 0.6),
    Sec("AAPL", "Apple Inc.", EQUITY, 171.30, 243.90, 0.055, 1.8),
    Sec("NVDA", "NVIDIA Corporation", EQUITY, 42.15, 168.20, 0.090, 0.3),
    Sec("FXAIX", "Fidelity 500 Index Fund", MUTUAL_FUND, 152.80, 219.40, 0.033, 2.9),
    Sec("VTIVX", "Vanguard Target Retirement 2045 Fund", MUTUAL_FUND, 24.60, 31.05, 0.025, 1.4),
    Sec("BTC", "Bitcoin", CRYPTO, 26_400.00, 71_800.00, 0.140, 0.9),
    # Money-market sweep. Fixed at $1.00 by construction, which is exactly why
    # it must not be allowed to drag the portfolio's return toward zero.
    Sec("VMFXX", "Vanguard Federal Money Market Fund", CASH_EQUIVALENT, 1.00, 1.00, 0.0, 0.0),
]


@dataclass(frozen=True)
class Pos:
    """A position's share count over time, and how it was acquired.

    `start_qty` is the holding at the oldest snapshot; `monthly_qty` is added
    every month after that, which is what a payroll-deducted contribution or a
    recurring buy actually looks like. `basis_known=False` models the ACATS
    rollover whose cost basis the receiving custodian never populated.
    """

    ticker: str
    start_qty: float
    monthly_qty: float
    basis_known: bool = True
    # Distribution per share, paid in the last month of each calendar quarter.
    quarterly_dividend: float = 0.0


@dataclass(frozen=True)
class Acct:
    provider_id: str
    name: str
    mask: str
    subtype: str
    positions: tuple[Pos, ...] = ()
    # Trading fee charged once a year, to exercise the FEE path.
    annual_fee_dollars: float = 0.0


ACCOUNTS = [
    Acct(
        "DEMO_BROKERAGE", "Individual Brokerage", "3311", "brokerage",
        positions=(
            Pos("VTI", 62.0, 1.85, quarterly_dividend=0.94),
            Pos("VXUS", 140.0, 4.20, quarterly_dividend=0.78),
            Pos("AAPL", 45.0, 0.55),
            Pos("NVDA", 80.0, 1.10),
            Pos("BTC", 0.21, 0.004),
        ),
        annual_fee_dollars=35.00,
    ),
    Acct(
        "DEMO_ROTH", "Roth IRA", "7742", "roth",
        positions=(
            Pos("VTI", 88.0, 1.30, quarterly_dividend=0.94),
            Pos("VXUS", 210.0, 3.10, quarterly_dividend=0.78),
            Pos("BND", 130.0, 1.90, quarterly_dividend=0.61),
            Pos("VNQ", 46.0, 0.60, quarterly_dividend=0.83),
        ),
    ),
    Acct(
        "DEMO_401K", "Fidelity 401(k)", "5580", "401k",
        positions=(
            Pos("FXAIX", 210.0, 3.40),
            # Rolled in from a former employer; basis did not survive the ACATS.
            Pos("VTIVX", 640.0, 6.80, basis_known=False),
            Pos("BND", 180.0, 2.20, quarterly_dividend=0.61),
        ),
    ),
    # A brokerage cash-management account, which is how a high-yield sweep
    # actually appears through Plaid: `investment` type, cash-equivalent
    # holding. Modelling it as a depository savings account would make it
    # invisible to the portfolio drilldown the spec asks for.
    Acct(
        "DEMO_HYSA", "High-Yield Savings", "2204", "cash management",
        positions=(Pos("VMFXX", 14_200.0, 340.0),),
    ),
]


@dataclass(frozen=True)
class Liability:
    provider_id: str
    name: str
    mask: str
    type: str
    subtype: str
    # Balance today, as a positive amount owed — Plaid's own convention.
    current_dollars: float
    # Principal retired per month, walking the history backwards.
    monthly_principal: float


LIABILITIES = [
    Liability("DEMO_MORTGAGE", "Home Mortgage", "0092", "loan", "mortgage", 388_450.00, 742.00),
    Liability("DEMO_AUTOLOAN", "Auto Loan", "6631", "loan", "auto", 18_920.00, 486.00),
]


@dataclass(frozen=True)
class ManualAsset:
    provider_id: str
    name: str
    mask: str
    subtype: str
    current_dollars: float
    # Appreciation per month, walking the history backwards.
    monthly_change: float


# The house the mortgage is against. Without it net worth reads as if the user
# borrowed $388k and set it on fire, and every chart in Phase 4 would be a
# deep negative that says nothing about their finances.
MANUAL_ASSETS = [
    ManualAsset("DEMO_HOME", "Primary Residence", None, "real estate", 612_000.00, 1_150.00),
]


@dataclass
class Goal:
    name: str
    category: str
    target_dollars: float
    months_out: int | None
    accounts: tuple[str, ...] = ()
    manual_dollars: float = 0.0
    monthly_contribution_dollars: float | None = None
    notes: str | None = None


# Chosen to land one goal in each state the UI has to render: comfortably ahead,
# behind but reachable, unreachable at the current rate, and no deadline at all.
GOALS = [
    Goal(
        "Emergency Fund", GoalCategory.EMERGENCY_FUND.value, 22_000.00, 8,
        accounts=("DEMO_HYSA",),
        notes="Six months of core expenses, held in the sweep so it stays liquid.",
    ),
    Goal(
        "House Down Payment", GoalCategory.HOUSE_DOWN_PAYMENT.value, 150_000.00, 18,
        accounts=("DEMO_BROKERAGE",),
        monthly_contribution_dollars=2_500.00,
        notes="20% on a $750k target. Funded from the taxable brokerage.",
    ),
    Goal(
        "Financial Independence", GoalCategory.FIRE.value, 1_500_000.00, None,
        accounts=("DEMO_BROKERAGE", "DEMO_ROTH", "DEMO_401K"),
        monthly_contribution_dollars=4_000.00,
        notes="25x annual spend. No deadline on purpose.",
    ),
    # Target is the *original* principal, not what is left: a liability-backed
    # goal scores debt retired, so the target has to be the debt there was to
    # retire. $32,000 borrowed, $18,920 outstanding — 41% paid off.
    Goal(
        "Pay Off Auto Loan", GoalCategory.CAR_PURCHASE.value, 32_000.00, 24,
        accounts=("DEMO_AUTOLOAN",),
        monthly_contribution_dollars=486.00,
        notes="Linked to the loan itself: progress is debt retired, not cash saved.",
    ),
    Goal(
        "Kitchen Renovation", GoalCategory.CUSTOM.value, 45_000.00, 10,
        manual_dollars=6_400.00,
        monthly_contribution_dollars=900.00,
        notes="Not linked to an account, so progress is the manual baseline.",
    ),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _cents(dollars: float) -> int:
    return int(Decimal(str(dollars)).scaleb(2).quantize(Decimal("1"), ROUND_HALF_UP))


def _qty(value: float) -> Decimal:
    return Decimal(str(round(value, 8)))


def _month_start(anchor: date, months_back: int) -> date:
    year, month = anchor.year, anchor.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _snapshot_dates(today: date) -> list[date]:
    """Month-end snapshot dates, oldest first, ending on today.

    Month *starts* would make every holding snapshot land on the same day as
    the demo ledger's rent payment, which is fine but reads oddly on a chart;
    day 28 is the last day every month has.
    """
    dates = [
        _month_start(today, months_back) + timedelta(days=27)
        for months_back in range(MONTHS_OF_HISTORY, 0, -1)
    ]
    return [d for d in dates if d < today] + [today]


def _institution(db, provider_id: str, name: str) -> Institution:
    institution = db.scalar(
        select(Institution).where(Institution.provider_institution_id == provider_id)
    )
    if institution is None:
        institution = Institution(provider_institution_id=provider_id, name=name)
        db.add(institution)
        db.flush()
    return institution


def _item(db, user, provider_item_id: str, institution: Institution) -> Item:
    item = db.scalar(
        select(Item).where(
            Item.user_id == user.id, Item.provider_item_id == provider_item_id
        )
    )
    if item is None:
        item = Item(
            user_id=user.id,
            institution_id=institution.id,
            provider_item_id=provider_item_id,
            access_token="demo-not-a-real-token",
            status="good",
        )
        db.add(item)
        db.flush()
    return item


# --------------------------------------------------------------------------- #
# Clear
# --------------------------------------------------------------------------- #

def _clear(db) -> dict:
    user = get_current_user(db)
    removed = {"accounts": 0, "holdings": 0, "investment_transactions": 0,
               "securities": 0, "goals": 0, "balance_snapshots": 0}

    items = list(
        db.scalars(
            select(Item).where(
                Item.user_id == user.id,
                Item.provider_item_id.in_([DEMO_INVEST_ITEM, DEMO_LOAN_ITEM]),
            )
        ).all()
    )
    account_ids = [
        a
        for item in items
        for a in db.scalars(select(Account.id).where(Account.item_id == item.id)).all()
    ]

    if account_ids:
        # Goal links first: the join row FKs both the goal and the account, and
        # the account cascade would take the link with it, but the goals
        # themselves are user-scoped and have to go explicitly.
        db.execute(
            delete(FinancialGoalAccount).where(
                FinancialGoalAccount.account_id.in_(account_ids)
            )
        )
        removed["holdings"] = db.execute(
            delete(Holding).where(Holding.account_id.in_(account_ids))
        ).rowcount
        removed["investment_transactions"] = db.execute(
            delete(InvestmentTransaction).where(
                InvestmentTransaction.account_id.in_(account_ids)
            )
        ).rowcount
        removed["balance_snapshots"] = db.execute(
            delete(BalanceSnapshot).where(BalanceSnapshot.account_id.in_(account_ids))
        ).rowcount
        removed["accounts"] = db.execute(
            delete(Account).where(Account.id.in_(account_ids))
        ).rowcount

    # By name, not by user: goals are user-scoped with nothing marking them as
    # demo data, and a --clear that wipes goals the user typed by hand is a
    # data-loss bug wearing a cleanup function's clothes.
    removed["goals"] = db.execute(
        delete(FinancialGoal).where(
            FinancialGoal.user_id == user.id,
            FinancialGoal.name.in_([g.name for g in GOALS]),
        )
    ).rowcount
    removed["securities"] = db.execute(
        delete(Security).where(Security.provider_security_id.like(f"{SECURITY_PREFIX}%"))
    ).rowcount

    for item in items:
        db.delete(item)
    db.commit()
    return removed


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

@dataclass
class SeedResult:
    securities: int = 0
    accounts: int = 0
    holdings: int = 0
    investment_transactions: int = 0
    balance_snapshots: int = 0
    goals: int = 0
    warnings: list[str] = field(default_factory=list)


def seed(db) -> dict:
    user = get_current_user(db)
    today = date.today()
    dates = _snapshot_dates(today)
    last = len(dates) - 1

    brokerage_item = _item(
        db, user, DEMO_INVEST_ITEM, _institution(db, "DEMO_INS_BROKER", "Demo Brokerage")
    )
    if db.scalar(select(Account.id).where(Account.item_id == brokerage_item.id)):
        return {"skipped": True}
    loan_item = _item(
        db, user, DEMO_LOAN_ITEM, _institution(db, "DEMO_INS_LENDER", "Demo Lending")
    )

    result = SeedResult()

    # --- securities -------------------------------------------------------- #
    securities: dict[str, Security] = {}
    for spec in SECURITIES:
        close = spec.price_at(last, last)
        # One trading day back, approximated off the same curve. This is what
        # makes the 1-day change column non-null without a price history table.
        previous = spec.price_at(last, last) * (1 - 0.004 * math.cos(spec.phase + 0.4))
        row = Security(
            provider_security_id=f"{SECURITY_PREFIX}{spec.ticker}",
            ticker_symbol=spec.ticker,
            name=spec.name,
            type=spec.type,
            asset_class=classify_asset_class(spec.ticker, spec.type, spec.name),
            close_price_cents=_cents(close),
            close_price_as_of=today,
            previous_close_price_cents=_cents(previous),
            is_cash_equivalent=spec.type == CASH_EQUIVALENT,
        )
        db.add(row)
        securities[spec.ticker] = row
        result.securities += 1
    db.flush()

    price_specs = {spec.ticker: spec for spec in SECURITIES}

    # --- investment accounts, holdings, trades ----------------------------- #
    accounts_by_provider: dict[str, Account] = {}
    seq = 0

    for spec in ACCOUNTS:
        account = Account(
            item_id=brokerage_item.id,
            provider_account_id=spec.provider_id,
            name=spec.name,
            mask=spec.mask,
            type="investment",
            subtype=spec.subtype,
            current_balance_cents=0,  # replaced below with the holdings total
        )
        db.add(account)
        db.flush()
        accounts_by_provider[spec.provider_id] = account
        result.accounts += 1

        # Cash a contribution has to cover, per month. Accumulated across every
        # position because one payroll deduction funds all of that month's buys,
        # not one per fund.
        funding_needed_cents: dict[date, int] = {}

        for position in spec.positions:
            security = securities[position.ticker]
            price_spec = price_specs[position.ticker]
            # Basis accumulates at the price actually paid each month, which is
            # the whole point of generating a price path rather than a flat one.
            basis_cents = _cents(position.start_qty * price_spec.price_at(0, last))

            for index, on in enumerate(dates):
                quantity = position.start_qty + position.monthly_qty * index
                price = price_spec.price_at(index, last)
                price_cents = _cents(price)
                value_cents = _cents(quantity * price)

                if index > 0 and position.monthly_qty:
                    seq += 1
                    bought_cents = _cents(position.monthly_qty * price)
                    basis_cents += bought_cents
                    funding_needed_cents[on] = funding_needed_cents.get(on, 0) + bought_cents
                    db.add(
                        InvestmentTransaction(
                            account_id=account.id,
                            security_id=security.id,
                            provider_investment_txn_id=f"DEMO-IT-{spec.provider_id}-{seq}",
                            type=InvestmentTransactionType.BUY.value,
                            subtype="buy",
                            # Negative: cash leaves the account to acquire shares.
                            amount_cents=-bought_cents,
                            quantity=_qty(position.monthly_qty),
                            price_cents=price_cents,
                            date=on,
                            description=f"Buy {position.ticker}",
                        )
                    )
                    result.investment_transactions += 1

                if position.quarterly_dividend and on.month % 3 == 0:
                    seq += 1
                    db.add(
                        InvestmentTransaction(
                            account_id=account.id,
                            security_id=security.id,
                            provider_investment_txn_id=f"DEMO-IT-{spec.provider_id}-{seq}",
                            type=InvestmentTransactionType.DIVIDEND.value,
                            subtype="dividend",
                            amount_cents=_cents(quantity * position.quarterly_dividend),
                            quantity=None,
                            price_cents=None,
                            date=on,
                            description=f"{position.ticker} dividend",
                        )
                    )
                    result.investment_transactions += 1

                db.add(
                    Holding(
                        account_id=account.id,
                        security_id=security.id,
                        as_of_date=on,
                        quantity=_qty(quantity),
                        institution_price_cents=price_cents,
                        institution_value_cents=value_cents,
                        cost_basis_cents=basis_cents if position.basis_known else None,
                        value_drift_cents=value_cents - round(quantity * price_cents),
                    )
                )
                result.holdings += 1

        # --- the contribution that pays for those buys --------------------- #
        # Without this the fixture is quietly incoherent: shares appear every
        # month, bought with cash that never entered the account. Nothing in
        # the app noticed, because a BUY is cash-neutral at the account level
        # and no code path asked where the cash came from.
        #
        # It matters beyond realism. TRANSFER is the *only* member of
        # EXTERNAL_FLOW_TYPES, so a fixture with no TRANSFER rows leaves
        # is_external_flow false on every row in the database. Every assertion
        # about contribution-versus-return then passes without evaluating
        # anything — the vacuous green this whole layer is built to avoid — and
        # return attribution, whose entire job is separating deposited money
        # from market movement, has no deposited money to separate.
        #
        # Dividends are deliberately NOT netted off the contribution. A
        # distribution is return the portfolio generated, not money the user
        # added; treating it as funding would understate contributions and
        # inflate measured return by exactly the dividend.
        for on, needed_cents in sorted(funding_needed_cents.items()):
            seq += 1
            db.add(
                InvestmentTransaction(
                    account_id=account.id,
                    security_id=None,  # cash arriving, not an instrument
                    provider_investment_txn_id=f"DEMO-IT-{spec.provider_id}-{seq}",
                    type=InvestmentTransactionType.TRANSFER.value,
                    subtype="contribution",
                    # Positive: cash enters the account, matching the ledger's
                    # sign convention rather than the trade's.
                    amount_cents=needed_cents,
                    quantity=None,
                    price_cents=None,
                    date=on,
                    description="Monthly contribution",
                )
            )
            result.investment_transactions += 1

        if spec.annual_fee_dollars:
            for index, on in enumerate(dates):
                if index and on.month == 1:
                    seq += 1
                    db.add(
                        InvestmentTransaction(
                            account_id=account.id,
                            security_id=None,
                            provider_investment_txn_id=f"DEMO-IT-{spec.provider_id}-{seq}",
                            type=InvestmentTransactionType.FEE.value,
                            subtype="account fee",
                            amount_cents=-_cents(spec.annual_fee_dollars),
                            date=on,
                            description="Annual account fee",
                        )
                    )
                    result.investment_transactions += 1

    db.flush()

    # The account balance has to agree with the holdings that back it, or the
    # net worth fallback path would contradict the portfolio page.
    for spec in ACCOUNTS:
        account = accounts_by_provider[spec.provider_id]
        total = 0
        for position in spec.positions:
            price_spec = price_specs[position.ticker]
            quantity = position.start_qty + position.monthly_qty * last
            total += _cents(quantity * price_spec.price_at(last, last))
        account.current_balance_cents = total
        account.available_balance_cents = total

    # --- liabilities ------------------------------------------------------- #
    for spec in LIABILITIES:
        account = Account(
            item_id=loan_item.id,
            provider_account_id=spec.provider_id,
            name=spec.name,
            mask=spec.mask,
            type=spec.type,
            subtype=spec.subtype,
            # Positive amount owed, matching what Plaid actually returns for a
            # loan. net_worth.signed_balance_cents derives the sign from the
            # account type, so this and demo.py's negative card balance both
            # come out right.
            current_balance_cents=_cents(spec.current_dollars),
        )
        db.add(account)
        db.flush()
        accounts_by_provider[spec.provider_id] = account
        result.accounts += 1

        for index, on in enumerate(dates):
            months_back = last - index
            db.add(
                BalanceSnapshot(
                    account_id=account.id,
                    date=on,
                    balance_cents=_cents(
                        spec.current_dollars + spec.monthly_principal * months_back
                    ),
                )
            )
            result.balance_snapshots += 1

    # --- manual assets ----------------------------------------------------- #
    for spec in MANUAL_ASSETS:
        account = Account(
            item_id=loan_item.id,
            provider_account_id=spec.provider_id,
            name=spec.name,
            mask=spec.mask,
            type="other",
            subtype=spec.subtype,
            current_balance_cents=_cents(spec.current_dollars),
        )
        db.add(account)
        db.flush()
        accounts_by_provider[spec.provider_id] = account
        result.accounts += 1

        for index, on in enumerate(dates):
            months_back = last - index
            db.add(
                BalanceSnapshot(
                    account_id=account.id,
                    date=on,
                    balance_cents=_cents(
                        spec.current_dollars - spec.monthly_change * months_back
                    ),
                )
            )
            result.balance_snapshots += 1

    # --- goals ------------------------------------------------------------- #
    for spec in GOALS:
        goal = FinancialGoal(
            user_id=user.id,
            name=spec.name,
            category=spec.category,
            target_amount_cents=_cents(spec.target_dollars),
            current_amount_cents=_cents(spec.manual_dollars),
            target_date=(
                today + timedelta(days=round(spec.months_out * 30.44))
                if spec.months_out is not None
                else None
            ),
            monthly_contribution_cents=(
                _cents(spec.monthly_contribution_dollars)
                if spec.monthly_contribution_dollars is not None
                else None
            ),
            notes=spec.notes,
        )
        db.add(goal)
        db.flush()
        for provider_id in spec.accounts:
            db.add(
                FinancialGoalAccount(
                    goal_id=goal.id, account_id=accounts_by_provider[provider_id].id
                )
            )
        result.goals += 1

    db.commit()
    return result.__dict__.copy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Phase 4 demo data")
    parser.add_argument("--clear", action="store_true", help="Remove the Phase 4 demo data")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.clear:
            removed = _clear(db)
            print("Removed " + ", ".join(f"{v} {k}" for k, v in removed.items()))
            return 0
        result = seed(db)
        if result.get("skipped"):
            print("Phase 4 demo data already present; nothing to do.")
            return 0
        print(
            f"Created {result['securities']} securities, {result['accounts']} accounts, "
            f"{result['holdings']} holdings, "
            f"{result['investment_transactions']} investment transactions, "
            f"{result['balance_snapshots']} balance snapshots, {result['goals']} goals."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
