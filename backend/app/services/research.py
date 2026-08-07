"""The 4-stage fundamental research framework, assembled into a scorecard.

Reads the annual series produced by `xbrl` and the filing prose produced by
`filings`, applies the framework's thresholds, and returns a per-check
green/amber/red verdict with the number, the target and the provenance behind
it.

**A missing input is `UNKNOWN`, and `UNKNOWN` never counts as a pass.** This is
the single most important rule in the module. Every check here is one a person
might act on, and the failure mode of a screener is not being wrong loudly — it
is being wrong quietly. Concretely:

- Apple tags no interest-expense concept at all. Dividing by a missing value
  and calling it infinite coverage would award a green flag to a company with
  $90bn of debt purely because a number was absent. Coverage is only a pass
  when the balance sheet independently shows there is no meaningful debt to
  service.
- Negative shareholders' equity makes D/E and ROE arithmetically valid and
  financially meaningless — a company with -$5bn equity and $1bn of income
  reports an ROE of -20%, which reads as a mild loss rather than as the balance
  sheet being upside down. Both checks detect the sign and say what happened
  instead of publishing the ratio.
- A CAGR from a zero or negative base is not a growth rate at all.

**Where the framework is underspecified, the choice is documented on the check.**
"D/E below 1.5" does not say whether debt means interest-bearing borrowings or
total liabilities; the two differ by roughly 3x for the same company. This
module uses interest-bearing debt and reports the total-liabilities version
alongside it, so the reader can see both rather than trusting an unstated
convention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.services import filings, market_data, sec_client, xbrl
from app.services.market_data import PriceQuote

logger = logging.getLogger(__name__)

# Six period ends give the five intervals a "5-year CAGR" needs.
PERIOD_COUNT = 6

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

# Framework targets.
REVENUE_CAGR_TARGET_BPS = 1_000  # >10%
DEBT_TO_EQUITY_TARGET = Decimal("1.5")
INTEREST_COVERAGE_TARGET = Decimal("5.0")
ROE_TARGET_BPS = 1_500  # >15%
PEG_TARGET = Decimal("1.0")
PEG_FLAG = Decimal("2.0")
CUSTOMER_CONCENTRATION_THRESHOLD_BPS = 1_000  # >10% of revenue
# Below this, a board and management team have little personal exposure to the
# share price. Deliberately low: single-digit percentages are normal at mega-cap
# scale, so this is a WARN threshold, never a FAIL.
INSIDER_OWNERSHIP_TARGET_BPS = 100  # 1%
# Total debt under this fraction of revenue counts as "no meaningful debt", the
# only condition under which absent interest expense is read as a pass.
NEGLIGIBLE_DEBT_TO_REVENUE = Decimal("0.05")

# A stock split multiplies the share count without changing anything about the
# business. EDGAR's company facts only carry split-adjusted prior years inside
# filings made *after* the split, so a series assembled from the latest
# available filing per year has a permanent cliff in the middle of it: NVIDIA's
# diluted count jumps 2.54bn → 25.07bn at FY2023 solely because FY2023 was last
# restated by a post-split 10-K while FY2022 was not. Taken at face value that
# is 880% dilution, and the buyback check red-flags the most aggressive
# repurchaser in the index. Prior years are therefore restated onto the current
# basis, and the adjustment is disclosed as a note rather than applied silently.
SPLIT_TOLERANCE = Decimal("0.06")
# The largest splits US issuers actually do are 20-for-1 (Amazon and Alphabet,
# both 2022). Anything beyond this is a filer tagging its share count at the
# wrong scale, and must not be "corrected" into the series as if it were real.
SPLIT_MAX_RATIO = 50

# SIC major group 60 is depository institutions, 61 non-depository credit. For
# both, interest expense is the cost of the money they lend on rather than a
# financing charge, which breaks the interest-coverage check specifically.
LENDER_SIC_GROUPS = ("60", "61")


@dataclass
class Check:
    """One framework test, with everything needed to render and to audit it."""

    key: str
    label: str
    status: str
    value_display: str
    target_display: str
    # Why this verdict. Shown under the check, so a red flag is never bare.
    detail: str
    value_bps: int | None = None
    value_cents: int | None = None


@dataclass
class Stage:
    key: str
    number: int
    title: str
    description: str
    checks: list[Check] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Worst verdict wins, and unknowns cannot be papered over by passes."""
        statuses = {c.status for c in self.checks}
        if FAIL in statuses:
            return FAIL
        if WARN in statuses:
            return WARN
        if PASS in statuses:
            return UNKNOWN if UNKNOWN in statuses else PASS
        return UNKNOWN

    @property
    def counts(self) -> dict[str, int]:
        out = {PASS: 0, WARN: 0, FAIL: 0, UNKNOWN: 0}
        for check in self.checks:
            out[check.status] += 1
        return out


@dataclass
class AnnualRow:
    """One fiscal year, normalised. Money in integer cents throughout."""

    fiscal_label: str
    period_end: date
    revenue_cents: int | None = None
    gross_profit_cents: int | None = None
    gross_margin_bps: int | None = None
    operating_income_cents: int | None = None
    operating_margin_bps: int | None = None
    net_income_cents: int | None = None
    net_margin_bps: int | None = None
    diluted_shares: int | None = None
    diluted_eps: Decimal | None = None
    operating_cash_flow_cents: int | None = None
    capex_cents: int | None = None
    free_cash_flow_cents: int | None = None
    cash_and_sti_cents: int | None = None
    total_debt_cents: int | None = None
    long_term_debt_cents: int | None = None
    equity_cents: int | None = None
    total_liabilities_cents: int | None = None
    interest_expense_cents: int | None = None


@dataclass
class SourceFiling:
    form: str
    filed: date
    period: date | None
    document_url: str
    index_url: str


@dataclass
class ResearchReport:
    ticker: str
    cik: int
    company_name: str
    generated_at: date

    stages: list[Stage]
    annuals: list[AnnualRow]

    business_model: filings.BusinessModel | None
    concentration: filings.CustomerConcentration | None
    insider: filings.InsiderOwnership | None

    price: PriceQuote | None
    market_cap_cents: int | None
    pe_ratio: Decimal | None
    peg_ratio: Decimal | None
    eps_growth_bps: int | None

    sources: list[SourceFiling]
    # Concepts that had to be stitched across a taxonomy change, so the UI can
    # warn that a trend line mixes definitions.
    stitched_concepts: list[str]
    notes: list[str]

    @property
    def summary_counts(self) -> dict[str, int]:
        out = {PASS: 0, WARN: 0, FAIL: 0, UNKNOWN: 0}
        for stage in self.stages:
            for key, value in stage.counts.items():
                out[key] += value
        return out


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def _money(cents: int | None) -> str:
    if cents is None:
        return "—"
    dollars = cents / 100
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(dollars) >= limit:
            return f"${dollars / limit:,.2f}{suffix}"
    return f"${dollars:,.0f}"


def _pct(bps: int | None, digits: int = 1) -> str:
    return "—" if bps is None else f"{bps / 100:.{digits}f}%"


def _ratio(value: Decimal | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}x"


def _shares(count: int | None) -> str:
    if count is None:
        return "—"
    return f"{count / 1e9:.2f}B" if count >= 1e9 else f"{count / 1e6:.0f}M"


def _cents(value: Decimal | None) -> int | None:
    return None if value is None else xbrl.to_cents(value)


# --------------------------------------------------------------------------- #
# Building the annual table
# --------------------------------------------------------------------------- #


def _sanity_check_shares(
    shares: Decimal | None, net_income: Decimal | None, eps: Decimal | None
) -> Decimal | None:
    """Correct a share count the filer tagged at the wrong scale.

    McDonald's presents its income statement in millions and, for four of the
    last six years, tagged the diluted count as literally `751.8` shares — the
    number as printed, with the scaling factor lost. SEC republishes what the
    filer said, so the error survives into company facts and turns a 4%
    reduction in share count into a 1,000,000x reverse split.

    Net income divided by diluted EPS independently implies the count, from two
    figures on the face of a statement that gets far more scrutiny than the
    XBRL attributes do. It is not exact — EPS is struck on income available to
    common, so preferred dividends make it differ by a percent or two — which is
    why it only overrides the tagged value when the two disagree by more than
    5x. Nothing short of a scaling error disagrees by that much.
    """
    if shares is None:
        return None
    if net_income is None or eps is None or eps <= 0 or net_income <= 0:
        return shares
    implied = net_income / eps
    if implied <= 0 or shares <= 0:
        return shares
    if max(shares, implied) / min(shares, implied) < 5:
        return shares
    logger.info("Share count %s rescaled to %s from net income / EPS", shares, implied)
    return implied


def _whole_split_ratio(ratio: Decimal) -> int | None:
    """The whole-number split `ratio` is within tolerance of, or None.

    Only whole numbers from 2 upward count. Companies do double their share
    count for real — an all-stock merger will do it — but that lands on 1.83x or
    2.31x, not within 6% of exactly 2. The tolerance has to be this wide because
    the weighted-average count drifts a percent or two a year on its own, which
    is why NVIDIA's 10-for-1 shows up as 9.89x rather than 10.00x.
    """
    nearest = int(ratio.to_integral_value(rounding=ROUND_HALF_UP))
    if nearest < 2 or nearest > SPLIT_MAX_RATIO:
        return None
    if abs(ratio - nearest) / nearest > SPLIT_TOLERANCE:
        return None
    return nearest


def _normalise_splits(rows: list[AnnualRow]) -> list[str]:
    """Restate pre-split years onto the newest year's share basis, in place."""
    factor = Decimal(1)
    notes: list[str] = []

    # Newest first, because the current basis is the one worth expressing the
    # history in, and every split found applies to everything older than it.
    for index in range(len(rows) - 1, 0, -1):
        newer, older = rows[index], rows[index - 1]
        if newer.diluted_shares and older.diluted_shares:
            ratio = Decimal(newer.diluted_shares) / (
                Decimal(older.diluted_shares) * factor
            )
            forward = _whole_split_ratio(ratio)
            reverse = None if forward else _whole_split_ratio(Decimal(1) / ratio)
            if forward:
                factor *= forward
                notes.append(
                    f"{forward}-for-1 split between {older.fiscal_label} and "
                    f"{newer.fiscal_label}"
                )
            elif reverse:
                factor /= reverse
                notes.append(
                    f"1-for-{reverse} reverse split between {older.fiscal_label} "
                    f"and {newer.fiscal_label}"
                )

        if factor != 1:
            if older.diluted_shares is not None:
                older.diluted_shares = int(
                    (Decimal(older.diluted_shares) * factor).to_integral_value(
                        rounding=ROUND_HALF_UP
                    )
                )
            if older.diluted_eps is not None:
                older.diluted_eps = (older.diluted_eps / factor).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )

    if not notes:
        return []
    return [
        "Share counts and per-share figures for earlier years are restated for a "
        "detected " + ", ".join(reversed(notes)) + ". EDGAR only carries "
        "split-adjusted history inside filings made after the split, so the raw "
        "facts would otherwise show the split as dilution."
    ]


def _build_annuals(
    facts: xbrl.CompanyFacts, periods: list[xbrl.FiscalPeriod]
) -> tuple[list[AnnualRow], list[str], dict[str, xbrl.Series]]:
    """Normalise the filer's XBRL into one row per fiscal year."""
    series: dict[str, xbrl.Series] = {
        "revenue": facts.duration_series(xbrl.REVENUE, periods),
        "cost_of_revenue": facts.duration_series(xbrl.COST_OF_REVENUE, periods),
        "gross_profit": facts.duration_series(xbrl.GROSS_PROFIT, periods),
        "operating_income": facts.duration_series(xbrl.OPERATING_INCOME, periods),
        "net_income": facts.duration_series(xbrl.NET_INCOME, periods),
        "interest_expense": facts.duration_series(xbrl.INTEREST_EXPENSE, periods),
        "ocf": facts.duration_series(xbrl.OPERATING_CASH_FLOW, periods),
        "capex": facts.duration_series(xbrl.CAPEX, periods),
        "cash": facts.instant_series(xbrl.CASH, periods),
        "sti": facts.instant_series(xbrl.SHORT_TERM_INVESTMENTS, periods),
        "lt_debt": facts.instant_series(xbrl.LONG_TERM_DEBT, periods),
        "st_debt": facts.instant_series(xbrl.SHORT_TERM_DEBT, periods),
        "equity": facts.instant_series(xbrl.STOCKHOLDERS_EQUITY, periods),
        "equity_incl_nci": facts.instant_series(xbrl.TOTAL_EQUITY_INCL_NCI, periods),
        "assets": facts.instant_series(xbrl.TOTAL_ASSETS, periods),
        "liabilities": facts.instant_series(xbrl.TOTAL_LIABILITIES, periods),
        "diluted_shares": facts.duration_series(
            xbrl.DILUTED_SHARES, periods, unit="shares"
        ),
        "basic_shares": facts.duration_series(
            xbrl.BASIC_SHARES, periods, unit="shares"
        ),
        "eps": facts.duration_series(xbrl.DILUTED_EPS, periods, unit="USD/shares"),
    }

    rows: list[AnnualRow] = []
    for period in periods:
        row = AnnualRow(fiscal_label=period.display, period_end=period.end)

        revenue = series["revenue"].get(period.end)
        row.revenue_cents = _cents(revenue)

        gross = series["gross_profit"].get(period.end)
        if gross is None:
            # Many filers present a single-step income statement and never tag
            # GrossProfit; it is exactly revenue less cost of revenue.
            cost = series["cost_of_revenue"].get(period.end)
            if revenue is not None and cost is not None:
                gross = revenue - abs(cost)
        row.gross_profit_cents = _cents(gross)
        row.gross_margin_bps = xbrl.ratio_bps(gross, revenue)

        operating = series["operating_income"].get(period.end)
        row.operating_income_cents = _cents(operating)
        row.operating_margin_bps = xbrl.ratio_bps(operating, revenue)

        net_income = series["net_income"].get(period.end)
        row.net_income_cents = _cents(net_income)
        row.net_margin_bps = xbrl.ratio_bps(net_income, revenue)

        shares = series["diluted_shares"].get(period.end) or series[
            "basic_shares"
        ].get(period.end)
        row.diluted_eps = series["eps"].get(period.end)
        shares = _sanity_check_shares(shares, net_income, row.diluted_eps)
        row.diluted_shares = int(shares) if shares is not None else None

        ocf = series["ocf"].get(period.end)
        capex = series["capex"].get(period.end)
        row.operating_cash_flow_cents = _cents(ocf)
        # Capex is a cash outflow; filers tag it as a positive payment, but not
        # universally. abs() makes FCF independent of the sign convention.
        row.capex_cents = _cents(abs(capex)) if capex is not None else None
        if ocf is not None and capex is not None:
            row.free_cash_flow_cents = _cents(ocf - abs(capex))

        cash = series["cash"].get(period.end)
        sti = series["sti"].get(period.end)
        if cash is not None:
            row.cash_and_sti_cents = _cents(cash + (sti or 0))

        lt_debt = series["lt_debt"].get(period.end)
        st_debt = series["st_debt"].get(period.end)
        row.long_term_debt_cents = _cents(lt_debt)
        if lt_debt is not None or st_debt is not None:
            # Decimal(x or 0) rather than (x or 0): a filer with exactly zero
            # long-term debt tags Decimal("0"), which is falsy, and adding two
            # bare int zeros produces an int that has no .quantize().
            row.total_debt_cents = _cents(Decimal(lt_debt or 0) + Decimal(st_debt or 0))

        equity = series["equity"].get(period.end)
        row.equity_cents = _cents(equity)

        liabilities = series["liabilities"].get(period.end)
        if liabilities is None:
            # Coca-Cola and many others never tag total liabilities. Assets less
            # equity is an accounting identity, so this is exact, not estimated.
            assets = series["assets"].get(period.end)
            equity_all = series["equity_incl_nci"].get(period.end)
            if assets is not None and equity_all is not None:
                liabilities = assets - equity_all
        row.total_liabilities_cents = _cents(liabilities)

        interest = series["interest_expense"].get(period.end)
        row.interest_expense_cents = (
            _cents(abs(interest)) if interest is not None else None
        )

        rows.append(row)

    stitched = sorted(
        {
            f"{name}: {' → '.join(s.concepts_used)}"
            for name, s in series.items()
            if s.is_stitched
        }
    )
    return rows, stitched, series


# --------------------------------------------------------------------------- #
# Stage 1 — qualitative and moat
# --------------------------------------------------------------------------- #


def _stage_qualitative(
    model: filings.BusinessModel | None,
    concentration: filings.CustomerConcentration | None,
    insider: filings.InsiderOwnership | None,
) -> Stage:
    stage = Stage(
        key="qualitative",
        number=1,
        title="Qualitative & Moat",
        description="Business model, customer concentration and insider alignment, read from the latest 10-K (Items 1 and 1A) and DEF 14A proxy.",
    )

    if model is None or model.classification == "UNKNOWN":
        stage.checks.append(
            Check(
                key="business_model",
                label="Revenue durability",
                status=UNKNOWN,
                value_display="—",
                target_display="Recurring",
                detail="Item 1 did not carry enough signal to classify the revenue model either way.",
            )
        )
    else:
        status = {
            "RECURRING": PASS,
            "MIXED": WARN,
            # Not a defect — a one-off revenue model is simply less durable, and
            # the framework treats durability as the green flag.
            "TRANSACTIONAL": WARN,
        }[model.classification]
        detail = (
            f"Language in Item 1/1A scores {model.recurring_score} for recurring "
            f"revenue against {model.transactional_score} for one-off "
            f"({model.confidence.lower()} confidence)."
        )
        stage.checks.append(
            Check(
                key="business_model",
                label="Revenue durability",
                status=status,
                value_display=model.classification.title(),
                target_display="Recurring",
                detail=detail,
            )
        )

    if concentration is None or concentration.status == "UNKNOWN":
        stage.checks.append(
            Check(
                key="customer_concentration",
                label="Customer concentration",
                status=UNKNOWN,
                value_display="—",
                target_display="No customer >10%",
                detail="No single-customer revenue disclosure was found. Filers need not disclose one when no customer exceeds 10%, so this is not evidence of concentration either way.",
            )
        )
    else:
        concentrated = concentration.status == "CONCENTRATED"
        if concentration.max_customer_bps is not None:
            value = _pct(concentration.max_customer_bps)
            detail = (
                f"Largest single customer disclosed at {value} of revenue."
                if concentrated
                else f"Largest single customer disclosed at {value}, inside the 10% threshold."
            )
        else:
            value = "None >10%"
            detail = "The filing states that no single customer reached 10% of revenue."
        stage.checks.append(
            Check(
                key="customer_concentration",
                label="Customer concentration",
                status=FAIL if concentrated else PASS,
                value_display=value,
                target_display="No customer >10%",
                detail=detail,
                value_bps=concentration.max_customer_bps,
            )
        )

    if insider is None or insider.status == "UNKNOWN":
        stage.checks.append(
            Check(
                key="insider_ownership",
                label="Insider & executive ownership",
                status=UNKNOWN,
                value_display="—",
                target_display="≥1% as a group",
                detail="The beneficial ownership table in the proxy could not be read.",
            )
        )
    elif insider.status == "BELOW_ONE_PERCENT":
        people = (
            f" ({insider.group_person_count} people)"
            if insider.group_person_count
            else ""
        )
        stage.checks.append(
            Check(
                key="insider_ownership",
                label="Insider & executive ownership",
                status=WARN,
                value_display="<1%",
                target_display="≥1% as a group",
                detail=f"Directors and executive officers as a group{people} hold under 1%, footnoted as such in the proxy. Normal at mega-cap scale, but it is little personal exposure to the share price.",
            )
        )
    else:
        people = (
            f" ({insider.group_person_count} people)"
            if insider.group_person_count
            else ""
        )
        meets = (insider.group_bps or 0) >= INSIDER_OWNERSHIP_TARGET_BPS
        stage.checks.append(
            Check(
                key="insider_ownership",
                label="Insider & executive ownership",
                status=PASS if meets else WARN,
                value_display=_pct(insider.group_bps, 2),
                target_display="≥1% as a group",
                detail=f"Directors and executive officers as a group{people} hold {_pct(insider.group_bps, 2)} of shares outstanding.",
                value_bps=insider.group_bps,
            )
        )

    return stage


# --------------------------------------------------------------------------- #
# Stage 2 — income statement and growth
# --------------------------------------------------------------------------- #


def _stage_growth(rows: list[AnnualRow]) -> Stage:
    stage = Stage(
        key="growth",
        number=2,
        title="Income Statement & Growth",
        description="Five-year revenue compounding, margin direction and whether the share count is shrinking or growing.",
    )

    revenues = [(r, r.revenue_cents) for r in rows if r.revenue_cents is not None]
    if len(revenues) >= 2:
        first_row, first = revenues[0]
        last_row, last = revenues[-1]
        years = len(revenues) - 1
        cagr = xbrl.cagr_bps(Decimal(first), Decimal(last), years)
        if cagr is None:
            stage.checks.append(
                Check(
                    key="revenue_cagr",
                    label=f"{years}-year revenue CAGR",
                    status=UNKNOWN,
                    value_display="—",
                    target_display=">10%",
                    detail="Revenue in the base year was zero or negative, so a compound growth rate is undefined.",
                )
            )
        else:
            if cagr > REVENUE_CAGR_TARGET_BPS:
                status = PASS
            elif cagr >= 0:
                status = WARN
            else:
                status = FAIL
            stage.checks.append(
                Check(
                    key="revenue_cagr",
                    label=f"{years}-year revenue CAGR",
                    status=status,
                    value_display=_pct(cagr),
                    target_display=">10%",
                    detail=f"Revenue compounded from {_money(first)} in {first_row.fiscal_label} to {_money(last)} in {last_row.fiscal_label}.",
                    value_bps=cagr,
                )
            )
    else:
        stage.checks.append(
            Check(
                key="revenue_cagr",
                label="5-year revenue CAGR",
                status=UNKNOWN,
                value_display="—",
                target_display=">10%",
                detail="Fewer than two annual revenue figures are available.",
            )
        )

    for key, label, attr in (
        ("gross_margin", "Gross margin trend", "gross_margin_bps"),
        ("operating_margin", "Operating margin trend", "operating_margin_bps"),
    ):
        values = [getattr(r, attr) for r in rows]
        present = [v for v in values if v is not None]
        direction = xbrl.trend_direction(values)
        if direction == "UNKNOWN" or not present:
            stage.checks.append(
                Check(
                    key=key,
                    label=label,
                    status=UNKNOWN,
                    value_display="—",
                    target_display="Expanding",
                    detail="Not enough annual data to establish a direction.",
                )
            )
            continue
        status = {
            "EXPANDING": PASS,
            "STABLE": PASS,
            "MIXED": WARN,
            "CONTRACTING": FAIL,
        }[direction]
        stage.checks.append(
            Check(
                key=key,
                label=label,
                status=status,
                value_display=f"{direction.title()} · {_pct(present[-1])}",
                target_display="Expanding or stable",
                detail=f"Moved from {_pct(present[0])} to {_pct(present[-1])} across the period.",
                value_bps=present[-1],
            )
        )

    share_counts = [r.diluted_shares for r in rows]
    present_shares = [s for s in share_counts if s is not None]
    if len(present_shares) >= 2:
        direction = xbrl.trend_direction([Decimal(s) for s in share_counts if s])
        first, last = present_shares[0], present_shares[-1]
        change_bps = xbrl.ratio_bps(Decimal(last - first), Decimal(first))
        # Inverted against the other trends on purpose: a shrinking share count
        # is a buyback returning capital, and a growing one is dilution.
        if direction == "CONTRACTING":
            status, verdict = PASS, "Buybacks"
        elif direction == "STABLE":
            status, verdict = PASS, "Flat"
        elif direction == "EXPANDING":
            status, verdict = FAIL, "Dilution"
        else:
            status, verdict = WARN, "Mixed"
        stage.checks.append(
            Check(
                key="share_count",
                label="Shares outstanding trend",
                status=status,
                value_display=f"{verdict} · {_pct(change_bps)}",
                target_display="Flat or shrinking",
                detail=f"Diluted share count moved from {_shares(first)} to {_shares(last)}.",
                value_bps=change_bps,
            )
        )
    else:
        stage.checks.append(
            Check(
                key="share_count",
                label="Shares outstanding trend",
                status=UNKNOWN,
                value_display="—",
                target_display="Flat or shrinking",
                detail="Fewer than two annual diluted share counts are available.",
            )
        )

    return stage


# --------------------------------------------------------------------------- #
# Stage 3 — balance sheet health
# --------------------------------------------------------------------------- #


def _stage_balance_sheet(rows: list[AnnualRow], is_lender: bool = False) -> Stage:
    stage = Stage(
        key="balance_sheet",
        number=3,
        title="Balance Sheet Health",
        description="Liquidity against long-term debt, leverage, and whether operating profit comfortably covers interest.",
    )
    latest = rows[-1] if rows else None
    if latest is None:
        return stage

    liquidity = latest.cash_and_sti_cents
    lt_debt = latest.long_term_debt_cents
    if liquidity is None or lt_debt is None:
        stage.checks.append(
            Check(
                key="cash_vs_debt",
                label="Cash & short-term investments vs long-term debt",
                status=UNKNOWN,
                value_display="—",
                target_display="Cash ≥ long-term debt",
                detail="Cash or long-term debt is not tagged for the latest year.",
            )
        )
    else:
        covers = liquidity >= lt_debt
        net = liquidity - lt_debt
        stage.checks.append(
            Check(
                key="cash_vs_debt",
                label="Cash & short-term investments vs long-term debt",
                status=PASS if covers else WARN,
                value_display=f"{_money(liquidity)} vs {_money(lt_debt)}",
                target_display="Cash ≥ long-term debt",
                detail=(
                    f"Net cash position of {_money(net)}."
                    if covers
                    else f"Long-term debt exceeds liquid assets by {_money(-net)}."
                ),
                value_cents=net,
            )
        )

    equity = latest.equity_cents
    total_debt = latest.total_debt_cents
    if equity is not None and equity < 0:
        # Publishing a ratio here would be arithmetically fine and financially
        # misleading — see the module docstring.
        stage.checks.append(
            Check(
                key="debt_to_equity",
                label="Debt-to-equity",
                status=FAIL,
                value_display="Negative equity",
                target_display="<1.5x",
                detail=f"Shareholders' equity is {_money(equity)}. A debt-to-equity ratio against a negative denominator is not meaningful, so none is shown.",
                value_cents=equity,
            )
        )
    elif equity is None or total_debt is None or equity == 0:
        stage.checks.append(
            Check(
                key="debt_to_equity",
                label="Debt-to-equity",
                status=UNKNOWN,
                value_display="—",
                target_display="<1.5x",
                detail="Total debt or shareholders' equity is not tagged for the latest year.",
            )
        )
    else:
        ratio = Decimal(total_debt) / Decimal(equity)
        liabilities_note = ""
        if latest.total_liabilities_cents:
            all_in = Decimal(latest.total_liabilities_cents) / Decimal(equity)
            liabilities_note = (
                f" Counting all liabilities rather than only borrowings it is "
                f"{all_in:.2f}x."
            )
        stage.checks.append(
            Check(
                key="debt_to_equity",
                label="Debt-to-equity",
                status=PASS if ratio < DEBT_TO_EQUITY_TARGET else FAIL,
                value_display=_ratio(ratio),
                target_display="<1.5x",
                detail=f"{_money(total_debt)} of interest-bearing debt against {_money(equity)} of equity.{liabilities_note}",
                value_bps=int(ratio * 10_000),
            )
        )

    operating = latest.operating_income_cents
    interest = latest.interest_expense_cents
    revenue = latest.revenue_cents
    negligible_debt = (
        total_debt is not None
        and revenue is not None
        and revenue > 0
        and Decimal(total_debt) / Decimal(revenue) < NEGLIGIBLE_DEBT_TO_REVENUE
    )

    if is_lender:
        # JPMorgan's interest expense is $98bn against $73bn of operating income,
        # which scores 0.7x and reads as a company about to default. It is
        # nothing of the sort: almost all of that is interest paid to
        # depositors, which for a bank is the cost of its raw material and sits
        # above the operating income line at any other issuer. There is no
        # sensible way to divide one by the other, so no number is published.
        stage.checks.append(
            Check(
                key="interest_coverage",
                label="Interest coverage",
                status=UNKNOWN,
                value_display="Not applicable",
                target_display="≥5.0x",
                detail=(
                    "Interest is this filer's cost of funds rather than debt service, so "
                    "operating income over interest expense does not measure solvency here. "
                    "Bank capital adequacy is assessed on regulatory ratios that are not in XBRL."
                ),
            )
        )
    elif interest and operating is not None:
        coverage = Decimal(operating) / Decimal(interest)
        stage.checks.append(
            Check(
                key="interest_coverage",
                label="Interest coverage",
                status=PASS if coverage >= INTEREST_COVERAGE_TARGET else FAIL,
                value_display=_ratio(coverage, 1),
                target_display="≥5.0x",
                detail=f"Operating income of {_money(operating)} against interest expense of {_money(interest)}.",
                value_bps=int(coverage * 10_000),
            )
        )
    elif negligible_debt:
        stage.checks.append(
            Check(
                key="interest_coverage",
                label="Interest coverage",
                status=PASS,
                value_display="No material debt",
                target_display="≥5.0x",
                detail=f"Total debt of {_money(total_debt)} is under 5% of revenue, so there is no meaningful interest burden to cover.",
            )
        )
    else:
        stage.checks.append(
            Check(
                key="interest_coverage",
                label="Interest coverage",
                status=UNKNOWN,
                value_display="—",
                target_display="≥5.0x",
                detail=(
                    f"No interest expense concept is tagged, but the balance sheet carries {_money(total_debt)} of debt — "
                    "so this cannot be read as having nothing to cover. Apple and others report interest inside a net "
                    "'other income/(expense)' line that cannot be separated from XBRL."
                    if total_debt
                    else "Neither interest expense nor total debt is tagged for the latest year."
                ),
            )
        )

    return stage


# --------------------------------------------------------------------------- #
# Stage 4 — cash flow and valuation
# --------------------------------------------------------------------------- #


def _stage_cash_flow(
    rows: list[AnnualRow],
    price: PriceQuote | None,
    pe_ratio: Decimal | None,
    peg_ratio: Decimal | None,
    eps_growth_bps: int | None,
) -> Stage:
    stage = Stage(
        key="cash_flow",
        number=4,
        title="Cash Flow & Valuation",
        description="Whether reported profit converts into cash, the return on equity, and what the market is charging for the growth.",
    )
    latest = rows[-1] if rows else None
    if latest is None:
        return stage

    fcf = latest.free_cash_flow_cents
    net_income = latest.net_income_cents
    if fcf is None or net_income is None:
        stage.checks.append(
            Check(
                key="fcf_quality",
                label="Free cash flow vs net income",
                status=UNKNOWN,
                value_display="—",
                target_display="FCF ≥ net income",
                detail="Operating cash flow, capital expenditure or net income is not tagged for the latest year.",
            )
        )
    else:
        conversion_bps = xbrl.ratio_bps(Decimal(fcf), Decimal(net_income))
        if fcf < 0:
            status = FAIL
        elif fcf >= net_income:
            status = PASS
        else:
            status = WARN
        stage.checks.append(
            Check(
                key="fcf_quality",
                label="Free cash flow vs net income",
                status=status,
                value_display=f"{_money(fcf)} vs {_money(net_income)}",
                target_display="FCF ≥ net income",
                detail=f"Operating cash flow {_money(latest.operating_cash_flow_cents)} less capital expenditure {_money(latest.capex_cents)}. Cash conversion {_pct(conversion_bps, 0)} of reported profit.",
                value_cents=fcf,
                value_bps=conversion_bps,
            )
        )

    equity = latest.equity_cents
    prior_equity = rows[-2].equity_cents if len(rows) >= 2 else None
    if net_income is None or equity is None:
        stage.checks.append(
            Check(
                key="roe",
                label="Return on equity",
                status=UNKNOWN,
                value_display="—",
                target_display=">15%",
                detail="Net income or shareholders' equity is not tagged for the latest year.",
            )
        )
    elif equity < 0:
        stage.checks.append(
            Check(
                key="roe",
                label="Return on equity",
                status=UNKNOWN,
                value_display="Negative equity",
                target_display=">15%",
                detail=f"Equity of {_money(equity)} makes return on equity uninterpretable — the ratio would print as a negative return on a profitable company. Judge returns on capital instead.",
            )
        )
    else:
        # Average equity where the prior year is available: income is earned
        # across the year while equity is a point-in-time snapshot, and using
        # only the closing balance overstates ROE for any company growing its
        # equity base.
        if prior_equity is not None and prior_equity > 0:
            denominator = Decimal(equity + prior_equity) / 2
            basis = "average equity"
        else:
            denominator = Decimal(equity)
            basis = "closing equity"
        roe_bps = xbrl.ratio_bps(Decimal(net_income), denominator)
        stage.checks.append(
            Check(
                key="roe",
                label="Return on equity",
                status=PASS if (roe_bps or 0) > ROE_TARGET_BPS else FAIL,
                value_display=_pct(roe_bps),
                target_display=">15%",
                detail=f"Net income of {_money(net_income)} on {basis} of {_money(int(denominator))}.",
                value_bps=roe_bps,
            )
        )

    if pe_ratio is None:
        stage.checks.append(
            Check(
                key="pe_ratio",
                label="P/E ratio",
                status=UNKNOWN,
                value_display="—",
                target_display="Context-dependent",
                detail=(
                    "No share price is available. Set FINNHUB_API_KEY, hold the ticker in a linked account, or pass an explicit price to compute valuation."
                    if price is None
                    else "The latest fiscal year reports no positive diluted EPS, so a P/E is not meaningful."
                ),
            )
        )
    else:
        stage.checks.append(
            Check(
                key="pe_ratio",
                label="P/E ratio",
                status=PASS,
                value_display=f"{pe_ratio:.1f}",
                target_display="Context-dependent",
                detail=f"Share price {_money(price.price_cents) if price else '—'} against {latest.fiscal_label} diluted EPS of ${latest.diluted_eps}. Trailing full-year earnings, not the last four quarters.",
                value_bps=int(pe_ratio * 100),
            )
        )

    if peg_ratio is None:
        stage.checks.append(
            Check(
                key="peg_ratio",
                label="PEG ratio",
                status=UNKNOWN,
                value_display="—",
                target_display="~1.0, flag >2.0",
                detail=(
                    "Needs both a P/E and a positive earnings growth rate."
                    if pe_ratio is not None
                    else "Needs a share price and a positive earnings growth rate."
                )
                + " Earnings growth here is historical; a broker PEG uses forward estimates and will differ.",
            )
        )
    else:
        if peg_ratio > PEG_FLAG:
            status = FAIL
        elif peg_ratio <= PEG_TARGET:
            status = PASS
        else:
            status = WARN
        stage.checks.append(
            Check(
                key="peg_ratio",
                label="PEG ratio",
                status=status,
                value_display=f"{peg_ratio:.2f}",
                target_display="~1.0, flag >2.0",
                detail=f"P/E of {pe_ratio:.1f} against historical diluted EPS growth of {_pct(eps_growth_bps)}. Computed from realised growth, not forward estimates, so it is not comparable to a broker PEG.",
                value_bps=int(peg_ratio * 100),
            )
        )

    return stage


# --------------------------------------------------------------------------- #
# Valuation inputs
# --------------------------------------------------------------------------- #


def _valuation(
    rows: list[AnnualRow], price: PriceQuote | None
) -> tuple[Decimal | None, Decimal | None, int | None, int | None]:
    """`(pe, peg, eps_growth_bps, market_cap_cents)`."""
    latest = rows[-1] if rows else None
    if latest is None:
        return None, None, None, None

    market_cap = None
    if price is not None and latest.diluted_shares:
        market_cap = price.price_cents * latest.diluted_shares

    eps_history = [
        (r, r.diluted_eps) for r in rows if r.diluted_eps is not None and r.diluted_eps > 0
    ]
    eps_growth_bps = None
    if len(eps_history) >= 2:
        eps_growth_bps = xbrl.cagr_bps(
            eps_history[0][1], eps_history[-1][1], len(eps_history) - 1
        )

    pe = None
    if (
        price is not None
        and latest.diluted_eps is not None
        and latest.diluted_eps > 0
    ):
        pe = (Decimal(price.price_cents) / 100) / latest.diluted_eps

    peg = None
    # A PEG against negative or zero growth is not a cheap stock, it is an
    # undefined quantity — the sign flip would make a shrinking company look
    # like the best value on the screen.
    if pe is not None and eps_growth_bps is not None and eps_growth_bps > 0:
        peg = pe / (Decimal(eps_growth_bps) / 100)

    # Two decimals. Division leaves 28 significant digits, and a P/E carried to
    # the 20th place implies a precision the underlying EPS does not have.
    quantum = Decimal("0.01")
    if pe is not None:
        pe = pe.quantize(quantum, rounding=ROUND_HALF_UP)
    if peg is not None:
        peg = peg.quantize(quantum, rounding=ROUND_HALF_UP)
    return pe, peg, eps_growth_bps, market_cap


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _no_annual_data_message(symbol: str, cik: int, facts: xbrl.CompanyFacts) -> str:
    """Explain *why* a real ticker has no annual series, not just that it hasn't.

    The common cause is not an obscure company but a corporate reorganisation.
    SEC's ticker index points at the current registrant, and when a company
    reorganises under a holding company the ticker moves to a brand-new CIK on
    the day the 8-K12B is filed, leaving the entire operating history stranded
    at the predecessor. In July 2026 XOM resolved to a two-month-old CIK holding
    nothing but 10-Qs. "No data" is a confusing answer for that; "the ticker now
    points at a successor registrant" is one the reader can act on.
    """
    name = facts.entity_name or symbol
    annual = sec_client.latest_filing(cik, ("10-K", "20-F", "40-F"))
    if annual is None:
        return (
            f"{symbol} resolves to {name} (CIK {cik}), which has never filed an "
            "annual report. This is usually a successor registrant created by a "
            "holding-company reorganisation or a recent IPO; the operating "
            "history sits under the predecessor's CIK, which SEC's ticker index "
            "no longer points to."
        )
    return (
        f"{name} (CIK {cik}) has filed a {annual.form} but SEC's company facts "
        "API exposes no annual XBRL periods for it. Foreign private issuers "
        "reporting under IFRS are the usual case."
    )


def analyze(
    db: Session, ticker: str, price_override_cents: int | None = None
) -> ResearchReport:
    """Run the full four-stage framework for one ticker."""
    symbol = ticker.strip().upper()
    cik, company_name = sec_client.resolve_ticker(symbol)

    facts = xbrl.CompanyFacts(sec_client.company_facts(cik))
    periods = facts.recent_periods(PERIOD_COUNT)
    if not periods:
        raise sec_client.FilingNotAvailable(_no_annual_data_message(symbol, cik, facts))

    rows, stitched, _ = _build_annuals(facts, periods)
    notes: list[str] = _normalise_splits(rows)
    sources: list[SourceFiling] = []

    sic, sic_description = sec_client.industry(cik)
    is_lender = sic[:2] in LENDER_SIC_GROUPS
    if is_lender:
        notes.append(
            f"{sic_description or 'This filer'} is a lender. Stages 2-4 apply "
            "conventions built for operating companies: it has no gross margin, "
            "capital expenditure is not how it invests, and its leverage is "
            "understated because customer deposits are not tagged as debt."
        )

    # -- Stage 1 inputs: the filings that have to be downloaded and parsed --- #
    business_model = None
    concentration = None
    insider = None

    tenk = sec_client.latest_filing(cik, ("10-K", "20-F"))
    if tenk is not None:
        sources.append(
            SourceFiling(tenk.form, tenk.filing_date, tenk.report_date, tenk.document_url, tenk.filing_index_url)
        )
        try:
            text = filings.html_to_text(sec_client.filing_document(tenk))
            item1, item1a = filings.extract_business_and_risk(text)
            if item1 is None and item1a is None:
                notes.append(
                    "Item 1 and Item 1A could not be located in the latest annual "
                    "report, so the qualitative checks fall back to the whole document."
                )
            business_model = filings.classify_business_model(item1, item1a)
            concentration = filings.detect_customer_concentration(
                item1, item1a, text, CUSTOMER_CONCENTRATION_THRESHOLD_BPS
            )
        except sec_client.SECError as exc:
            notes.append(f"The annual report could not be read: {exc}")
    else:
        notes.append("No 10-K or 20-F is on file, so stage 1 could not be evaluated.")

    proxy = sec_client.latest_filing(cik, ("DEF 14A",))
    if proxy is not None:
        sources.append(
            SourceFiling(proxy.form, proxy.filing_date, proxy.report_date, proxy.document_url, proxy.filing_index_url)
        )
        try:
            insider = filings.extract_insider_ownership(
                filings.html_to_text(sec_client.filing_document(proxy))
            )
        except sec_client.SECError as exc:
            notes.append(f"The proxy statement could not be read: {exc}")
    else:
        notes.append("No DEF 14A proxy is on file, so insider ownership is unavailable.")

    # -- Stage 4 inputs ------------------------------------------------------ #
    price = market_data.resolve_price(db, symbol, price_override_cents)
    pe, peg, eps_growth_bps, market_cap = _valuation(rows, price)

    if price is not None and price.is_stale_close:
        notes.append(
            f"Valuation uses the {price.as_of or 'last known'} close stored for this "
            "security in your portfolio, not a live quote."
        )

    stages = [
        _stage_qualitative(business_model, concentration, insider),
        _stage_growth(rows),
        _stage_balance_sheet(rows, is_lender),
        _stage_cash_flow(rows, price, pe, peg, eps_growth_bps),
    ]

    if stitched:
        notes.append(
            "Some series are stitched across a change in the filer's XBRL "
            "concepts, so a trend may mix two definitions: "
            + "; ".join(stitched)
        )

    return ResearchReport(
        ticker=symbol,
        cik=cik,
        company_name=facts.entity_name or company_name,
        generated_at=date.today(),
        stages=stages,
        annuals=rows,
        business_model=business_model,
        concentration=concentration,
        insider=insider,
        price=price,
        market_cap_cents=market_cap,
        pe_ratio=pe,
        peg_ratio=peg,
        eps_growth_bps=eps_growth_bps,
        sources=sources,
        stitched_concepts=stitched,
        notes=notes,
    )
