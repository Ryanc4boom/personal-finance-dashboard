"""Portfolio analytics: cost basis, gain/loss, allocation and per-account drilldown.

**Everything here reads dated holdings, never "the current position".** A
position is `(account, security, as_of_date)`, so "the portfolio right now"
means *the newest snapshot each account has*, resolved per account rather than
globally. If the brokerage synced this morning and the 401(k) last synced on
Friday, a global `MAX(as_of_date)` would silently drop the 401(k) and report a
portfolio tens of thousands of dollars too small — with no error, which is the
dangerous kind of wrong. Resolving the date per account also drops sold
positions correctly: a security that stopped appearing in an account's newest
snapshot is gone, whereas "latest row per (account, security)" would resurrect
every position the user has ever closed and hold it on the books forever.

**Cost basis is nullable and that is load-bearing.** Brokerages routinely fail
to carry basis across an ACATS transfer. Treating unknown basis as zero would
report the entire position as gain — wrong in the direction that looks like good
news, and wrong on something that ends up on a tax return. Positions with
unknown basis therefore contribute to *value* but are excluded from the return
figures, and `cost_basis_coverage_bps` reports how much of the portfolio the
return number actually covers so a 62%-covered figure is not read as a fact
about the whole account.

**Cash sweep is excluded from return, included in value.** A money-market
position has basis equal to value by construction, so folding it into the
return denominator drags every performance figure toward zero for a reason that
has nothing to do with performance.

**No floats.** Quantities are `Decimal`, money is integer cents, and every ratio
is basis points (1 bp = 0.01%) computed by integer division. Percentages are
apportioned by largest remainder so an allocation chart's slices sum to exactly
100.00% instead of 99.98%.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Holding, Item, Security, User
from app.models.enums import AccountType, AssetClass, SecurityType

# Tickers whose exposure is not inferable from the instrument type alone.
# Deliberately small and explicit: this is a pragmatic seed list for the common
# core-portfolio funds, not an attempt to be a security master. Anything absent
# falls through to the type-based default and remains user-correctable, which is
# why `security.asset_class_locked` exists.
_TICKER_ASSET_CLASS: dict[str, AssetClass] = {
    # US broad market / large cap
    "VTI": AssetClass.US_EQUITY,
    "VOO": AssetClass.US_EQUITY,
    "SPY": AssetClass.US_EQUITY,
    "IVV": AssetClass.US_EQUITY,
    "ITOT": AssetClass.US_EQUITY,
    "VTSAX": AssetClass.US_EQUITY,
    "FXAIX": AssetClass.US_EQUITY,
    "QQQ": AssetClass.US_EQUITY,
    "VUG": AssetClass.US_EQUITY,
    "VTV": AssetClass.US_EQUITY,
    # International
    "VXUS": AssetClass.INTERNATIONAL_EQUITY,
    "VEA": AssetClass.INTERNATIONAL_EQUITY,
    "VWO": AssetClass.INTERNATIONAL_EQUITY,
    "IXUS": AssetClass.INTERNATIONAL_EQUITY,
    "EFA": AssetClass.INTERNATIONAL_EQUITY,
    "VTIAX": AssetClass.INTERNATIONAL_EQUITY,
    # Bonds
    "BND": AssetClass.FIXED_INCOME,
    "AGG": AssetClass.FIXED_INCOME,
    "BNDX": AssetClass.FIXED_INCOME,
    "VBTLX": AssetClass.FIXED_INCOME,
    "TLT": AssetClass.FIXED_INCOME,
    "VTIP": AssetClass.FIXED_INCOME,
    # REITs
    "VNQ": AssetClass.REAL_ESTATE,
    "SCHH": AssetClass.REAL_ESTATE,
    "IYR": AssetClass.REAL_ESTATE,
    "VNQI": AssetClass.REAL_ESTATE,
    # Crypto
    "BTC": AssetClass.CRYPTO,
    "ETH": AssetClass.CRYPTO,
    "GBTC": AssetClass.CRYPTO,
    "IBIT": AssetClass.CRYPTO,
}

# Fallback when the ticker is unknown. An ETF or mutual fund defaults to US
# equity because that is what the overwhelming majority of held funds are — but
# it is a guess, and it is the guess `asset_class_locked` exists to let the user
# overrule.
_TYPE_ASSET_CLASS: dict[str, AssetClass] = {
    SecurityType.EQUITY.value: AssetClass.US_EQUITY,
    SecurityType.ETF.value: AssetClass.US_EQUITY,
    SecurityType.MUTUAL_FUND.value: AssetClass.US_EQUITY,
    SecurityType.CRYPTO.value: AssetClass.CRYPTO,
    SecurityType.FIXED_INCOME.value: AssetClass.FIXED_INCOME,
    SecurityType.CASH_EQUIVALENT.value: AssetClass.CASH,
}

# Name fragments that override the type default. Catches the long tail of funds
# absent from the ticker table — "Vanguard Total International Stock Index".
_NAME_HINTS: tuple[tuple[str, AssetClass], ...] = (
    ("international", AssetClass.INTERNATIONAL_EQUITY),
    ("ex-us", AssetClass.INTERNATIONAL_EQUITY),
    ("emerging market", AssetClass.INTERNATIONAL_EQUITY),
    ("developed market", AssetClass.INTERNATIONAL_EQUITY),
    ("global", AssetClass.INTERNATIONAL_EQUITY),
    ("real estate", AssetClass.REAL_ESTATE),
    ("reit", AssetClass.REAL_ESTATE),
    ("bond", AssetClass.FIXED_INCOME),
    ("treasury", AssetClass.FIXED_INCOME),
    ("fixed income", AssetClass.FIXED_INCOME),
    ("aggregate", AssetClass.FIXED_INCOME),
    ("bitcoin", AssetClass.CRYPTO),
    ("ethereum", AssetClass.CRYPTO),
    ("money market", AssetClass.CASH),
    ("cash reserve", AssetClass.CASH),
)


def classify_asset_class(
    ticker_symbol: str | None, security_type: str, name: str | None = None
) -> str:
    """Infer exposure from ticker, then name, then instrument type.

    Ordered strongest evidence first, mirroring the layered resolution Phase 2
    used for categories. Never raises: an unrecognised instrument lands in
    US_EQUITY rather than failing a sync, because a mildly miscategorised
    allocation chart is recoverable and a sync that aborts is not.
    """
    if ticker_symbol:
        hit = _TICKER_ASSET_CLASS.get(ticker_symbol.strip().upper())
        if hit is not None:
            return hit.value

    if name:
        lowered = name.lower()
        for fragment, asset_class in _NAME_HINTS:
            if fragment in lowered:
                return asset_class.value

    return _TYPE_ASSET_CLASS.get(security_type, AssetClass.US_EQUITY).value


# --------------------------------------------------------------------------- #
# Ratios
# --------------------------------------------------------------------------- #

def _bps(part: int, whole: int) -> int | None:
    """`part/whole` in basis points, or None when the ratio is meaningless.

    Integer arithmetic throughout: a gain percentage derived through float would
    disagree with the cents it was computed from in the last decimal place, and
    a portfolio page that shows +12.34% next to numbers implying +12.35% is a
    page nobody trusts twice.
    """
    if whole == 0:
        return None
    return round(part * 10_000 / whole)


def _apportion_bps(values: list[int]) -> list[int]:
    """Split 10,000 bps across `values` in proportion, summing to exactly 10,000.

    Largest-remainder: floor every share, then hand the leftover bps out one at
    a time to whoever was rounded down hardest. Naive rounding leaves an
    allocation chart summing to 99.97%, and the missing slice is visible.
    """
    total = sum(values)
    if total <= 0:
        return [0] * len(values)

    scaled = [v * 10_000 for v in values]
    floors = [s // total for s in scaled]
    leftover = 10_000 - sum(floors)

    order = sorted(range(len(values)), key=lambda i: scaled[i] % total, reverse=True)
    for i in order[:leftover]:
        floors[i] += 1
    return floors


# --------------------------------------------------------------------------- #
# Snapshot resolution
# --------------------------------------------------------------------------- #

def investment_accounts(db: Session, user: User) -> list[Account]:
    return list(
        db.scalars(
            select(Account)
            .join(Item, Account.item_id == Item.id)
            .where(
                Item.user_id == user.id,
                Account.is_active.is_(True),
                Account.type == AccountType.investment.value,
            )
            .order_by(Account.name.asc())
        ).all()
    )


def latest_holdings(
    db: Session, user: User, as_of: date | None = None
) -> list[Holding]:
    """Every position from each account's newest snapshot on or before `as_of`.

    The date is resolved *per account* — see the module docstring for why a
    global maximum silently truncates the portfolio.
    """
    accounts = investment_accounts(db, user)
    if not accounts:
        return []

    holdings: list[Holding] = []
    for account in accounts:
        stmt = select(Holding.as_of_date).where(Holding.account_id == account.id)
        if as_of is not None:
            stmt = stmt.where(Holding.as_of_date <= as_of)
        snapshot_date = db.scalar(stmt.order_by(Holding.as_of_date.desc()).limit(1))
        if snapshot_date is None:
            continue

        holdings.extend(
            db.scalars(
                select(Holding).where(
                    Holding.account_id == account.id,
                    Holding.as_of_date == snapshot_date,
                )
            ).all()
        )
    return holdings


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

@dataclass
class PositionAccount:
    """One account's slice of an aggregated position."""

    account_id: uuid.UUID
    account_name: str
    account_mask: str | None
    account_subtype: str | None
    quantity: Decimal
    value_cents: int
    cost_basis_cents: int | None
    as_of_date: date


@dataclass
class Position:
    """One security, summed across every account that holds it.

    Aggregated because "how much VTI do I own" is a portfolio question, not an
    account question — the same fund in a Roth and a taxable account is one
    exposure. The per-account split is kept in `accounts` so the drilldown never
    needs a second query.
    """

    security_id: uuid.UUID
    ticker_symbol: str | None
    name: str
    type: str
    asset_class: str
    is_cash_equivalent: bool

    quantity: Decimal
    price_cents: int | None
    price_as_of: date | None
    value_cents: int
    # None when *no* contributing account reported a basis.
    cost_basis_cents: int | None
    gain_cents: int | None
    gain_bps: int | None
    day_change_cents: int | None
    # Share of total portfolio value, in basis points.
    weight_bps: int
    accounts: list[PositionAccount] = field(default_factory=list)


@dataclass
class AllocationSlice:
    asset_class: str
    value_cents: int
    weight_bps: int
    position_count: int


@dataclass
class AccountAllocation:
    account_id: uuid.UUID
    name: str
    mask: str | None
    subtype: str | None
    value_cents: int
    cost_basis_cents: int | None
    gain_cents: int | None
    gain_bps: int | None
    day_change_cents: int | None
    weight_bps: int
    position_count: int
    as_of_date: date | None
    by_asset_class: list[AllocationSlice] = field(default_factory=list)


@dataclass
class PortfolioSummary:
    as_of_date: date | None
    total_value_cents: int
    # Basis of the positions that reported one — never a stand-in for the whole
    # portfolio. Read it with `cost_basis_coverage_bps`.
    total_cost_basis_cents: int
    # Value of the positions backing that basis, so gain % has a denominator
    # that matches its numerator.
    cost_basis_value_cents: int
    unrealized_gain_cents: int
    unrealized_gain_bps: int | None
    # What fraction of portfolio value the return figures actually describe.
    cost_basis_coverage_bps: int
    day_change_cents: int
    day_change_bps: int | None
    # Positions whose security had no previous close, so the day change omits
    # them. Surfaced rather than hidden — a day change covering half the
    # portfolio should not be presented as the portfolio's day change.
    day_change_missing_count: int
    cash_value_cents: int
    invested_value_cents: int
    position_count: int
    account_count: int


@dataclass
class PortfolioReport:
    summary: PortfolioSummary
    positions: list[Position] = field(default_factory=list)
    by_asset_class: list[AllocationSlice] = field(default_factory=list)
    by_account: list[AccountAllocation] = field(default_factory=list)


def build_portfolio(
    db: Session, user: User, as_of: date | None = None
) -> PortfolioReport:
    """Aggregate the newest holdings into positions, allocation and drilldown.

    One pass over the holdings builds all three views. They are returned
    together because they must agree: three endpoints recomputing totals
    independently is how a dashboard ends up showing a portfolio value that does
    not match the sum of its own allocation chart.
    """
    holdings = latest_holdings(db, user, as_of=as_of)
    accounts = {a.id: a for a in investment_accounts(db, user)}

    positions: dict[uuid.UUID, Position] = {}
    account_rollup: dict[uuid.UUID, dict] = {}
    latest_snapshot: date | None = None

    for holding in holdings:
        security: Security = holding.security
        value = holding.institution_value_cents
        basis = holding.cost_basis_cents

        if latest_snapshot is None or holding.as_of_date > latest_snapshot:
            latest_snapshot = holding.as_of_date

        position = positions.get(security.id)
        if position is None:
            position = Position(
                security_id=security.id,
                ticker_symbol=security.ticker_symbol,
                name=security.name,
                type=security.type,
                asset_class=security.asset_class,
                is_cash_equivalent=security.is_cash_equivalent,
                quantity=Decimal(0),
                price_cents=security.close_price_cents,
                price_as_of=security.close_price_as_of,
                value_cents=0,
                cost_basis_cents=None,
                gain_cents=None,
                gain_bps=None,
                day_change_cents=None,
                weight_bps=0,
            )
            positions[security.id] = position

        position.quantity += holding.quantity
        position.value_cents += value
        if basis is not None:
            position.cost_basis_cents = (position.cost_basis_cents or 0) + basis

        # A day change needs both closes. Missing either means the change is
        # unknown for this security, not zero — see PortfolioSummary.
        if (
            security.close_price_cents is not None
            and security.previous_close_price_cents is not None
        ):
            delta = security.close_price_cents - security.previous_close_price_cents
            change = int(
                (Decimal(delta) * holding.quantity).to_integral_value(rounding="ROUND_HALF_UP")
            )
            position.day_change_cents = (position.day_change_cents or 0) + change

        account = accounts.get(holding.account_id)
        if account is not None:
            position.accounts.append(
                PositionAccount(
                    account_id=account.id,
                    account_name=account.name,
                    account_mask=account.mask,
                    account_subtype=account.subtype,
                    quantity=holding.quantity,
                    value_cents=value,
                    cost_basis_cents=basis,
                    as_of_date=holding.as_of_date,
                )
            )

            rollup = account_rollup.setdefault(
                account.id,
                {
                    "value": 0,
                    "basis": 0,
                    "basis_value": 0,
                    "has_basis": False,
                    "day_change": 0,
                    "positions": 0,
                    "as_of": holding.as_of_date,
                    "by_class": {},
                },
            )
            rollup["value"] += value
            rollup["positions"] += 1
            rollup["as_of"] = max(rollup["as_of"], holding.as_of_date)
            if basis is not None and not security.is_cash_equivalent:
                rollup["basis"] += basis
                rollup["basis_value"] += value
                rollup["has_basis"] = True
            if (
                security.close_price_cents is not None
                and security.previous_close_price_cents is not None
            ):
                delta = security.close_price_cents - security.previous_close_price_cents
                rollup["day_change"] += int(
                    (Decimal(delta) * holding.quantity).to_integral_value(
                        rounding="ROUND_HALF_UP"
                    )
                )
            bucket = rollup["by_class"].setdefault(
                security.asset_class, {"value": 0, "count": 0}
            )
            bucket["value"] += value
            bucket["count"] += 1

    ordered = sorted(positions.values(), key=lambda p: p.value_cents, reverse=True)
    total_value = sum(p.value_cents for p in ordered)

    # Gain per position, and the portfolio denominators. Cash equivalents are
    # excluded from return: their basis equals their value by construction.
    total_basis = 0
    basis_value = 0
    day_change = 0
    day_change_missing = 0
    cash_value = 0

    for position in ordered:
        if position.cost_basis_cents is not None and not position.is_cash_equivalent:
            position.gain_cents = position.value_cents - position.cost_basis_cents
            position.gain_bps = _bps(position.gain_cents, position.cost_basis_cents)
            total_basis += position.cost_basis_cents
            basis_value += position.value_cents
        if position.day_change_cents is None:
            day_change_missing += 1
        else:
            day_change += position.day_change_cents
        if position.is_cash_equivalent:
            cash_value += position.value_cents

    for position, weight in zip(ordered, _apportion_bps([p.value_cents for p in ordered])):
        position.weight_bps = weight
        position.accounts.sort(key=lambda a: a.value_cents, reverse=True)

    # ---- allocation by asset class ---------------------------------------- #
    class_totals: dict[str, dict] = {}
    for position in ordered:
        bucket = class_totals.setdefault(
            position.asset_class, {"value": 0, "count": 0}
        )
        bucket["value"] += position.value_cents
        bucket["count"] += 1

    class_rows = sorted(
        class_totals.items(), key=lambda kv: kv[1]["value"], reverse=True
    )
    class_weights = _apportion_bps([v["value"] for _, v in class_rows])
    by_asset_class = [
        AllocationSlice(
            asset_class=name,
            value_cents=data["value"],
            weight_bps=weight,
            position_count=data["count"],
        )
        for (name, data), weight in zip(class_rows, class_weights)
    ]

    # ---- per-account drilldown -------------------------------------------- #
    account_rows = sorted(
        account_rollup.items(), key=lambda kv: kv[1]["value"], reverse=True
    )
    account_weights = _apportion_bps([v["value"] for _, v in account_rows])
    by_account: list[AccountAllocation] = []

    for (account_id, data), weight in zip(account_rows, account_weights):
        account = accounts[account_id]
        gain = data["basis_value"] - data["basis"] if data["has_basis"] else None
        inner_rows = sorted(
            data["by_class"].items(), key=lambda kv: kv[1]["value"], reverse=True
        )
        inner_weights = _apportion_bps([v["value"] for _, v in inner_rows])
        by_account.append(
            AccountAllocation(
                account_id=account_id,
                name=account.name,
                mask=account.mask,
                subtype=account.subtype,
                value_cents=data["value"],
                cost_basis_cents=data["basis"] if data["has_basis"] else None,
                gain_cents=gain,
                gain_bps=_bps(gain, data["basis"]) if data["has_basis"] else None,
                day_change_cents=data["day_change"],
                weight_bps=weight,
                position_count=data["positions"],
                as_of_date=data["as_of"],
                by_asset_class=[
                    AllocationSlice(
                        asset_class=name,
                        value_cents=inner["value"],
                        weight_bps=inner_weight,
                        position_count=inner["count"],
                    )
                    for (name, inner), inner_weight in zip(inner_rows, inner_weights)
                ],
            )
        )

    gain_total = basis_value - total_basis
    summary = PortfolioSummary(
        as_of_date=latest_snapshot,
        total_value_cents=total_value,
        total_cost_basis_cents=total_basis,
        cost_basis_value_cents=basis_value,
        unrealized_gain_cents=gain_total,
        unrealized_gain_bps=_bps(gain_total, total_basis),
        cost_basis_coverage_bps=_bps(basis_value, total_value) or 0,
        day_change_cents=day_change,
        # Yesterday's value is today's minus the change — the correct
        # denominator for a daily return.
        day_change_bps=_bps(day_change, total_value - day_change),
        day_change_missing_count=day_change_missing,
        cash_value_cents=cash_value,
        invested_value_cents=total_value - cash_value,
        position_count=len(ordered),
        account_count=len(account_rollup),
    )

    return PortfolioReport(
        summary=summary,
        positions=ordered,
        by_asset_class=by_asset_class,
        by_account=by_account,
    )


def portfolio_value_cents(db: Session, user: User, as_of: date | None = None) -> int:
    """Total investment value on a date. The hot path for net worth snapshots."""
    return sum(h.institution_value_cents for h in latest_holdings(db, user, as_of=as_of))
