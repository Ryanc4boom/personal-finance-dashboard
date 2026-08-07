"""Turning SEC `companyfacts` into a clean annual financial series.

The companyfacts blob is not a financial statement. It is every fact the filer
has ever tagged, from every filing, with no deduplication and no notion of which
value is current. Five traps sit between it and a usable five-year series, and
each one produces a plausible-looking wrong number rather than an error:

**1. `fy` and `fp` describe the filing, not the fact.** A FY2025 10-K restates
FY2023 and FY2024 for comparison, and *every* fact in it — including the
two-year-old ones — carries `fy: 2025, fp: "FY"`. Grouping by `fy` therefore
collapses three different years into one and silently picks whichever the dict
iterated last. **The period is derived from `start`/`end` here and `fy` is never
read.**

**2. Duration facts are mixed granularity.** The same concept holds annual,
quarterly and year-to-date periods in one array. Only facts spanning 340-400
days are annual; a nine-month YTD figure taken for a full year understates
revenue by a quarter and looks entirely reasonable on a chart.

**3. The same period appears many times, with different values.** Each year is
re-reported in the two following 10-Ks, plus amendments, and the values can
differ after a restatement or a segment reclassification. We take the value from
the most recently filed accession, so the series reads as currently restated
rather than as a mixture of vintages.

**4. Concepts change under the filer's feet.** ASC 606 moved most filers from
`SalesRevenueNet` to `RevenueFromContractWithCustomerExcludingAssessedTax` in
2018, and plenty of companies tag two revenue concepts simultaneously. Picking a
single alias by priority loses years; picking per-year by priority mixes
definitions mid-series. We choose **one primary concept — the alias covering the
most periods — and only fall back for the years it is missing**, then report
which concepts were used so a mixed series is visible rather than hidden.

**5. Fiscal years are not calendar years.** A 52/53-week filer's year can end on
2 January; NVIDIA's "fiscal 2026" ended 25 January 2026. Labels are derived from
the period end with an early-January correction, and all *arithmetic* is done on
period ends rather than labels so a mislabel can never misorder the series.

Money leaves this module as **integer cents**, per the project-wide convention.
Share counts stay integers. Per-share amounts stay `Decimal`, because rounding
EPS to cents before dividing it into a price would visibly move the P/E.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Sequence

# A duration this long is an annual period. The band is wide enough for 52/53
# week years (364/371 days) and for filers with a stub or extended transition
# year, and narrow enough to exclude a YTD nine-month figure (~273 days).
ANNUAL_MIN_DAYS = 340
ANNUAL_MAX_DAYS = 400

# Two period ends this close together are the same fiscal period re-reported.
# Comfortably wider than 52/53-week drift, far narrower than a fiscal quarter.
PERIOD_TOLERANCE_DAYS = 25

# Forms whose numbers are audited annual statements. Preferred over the same
# fact appearing in an 8-K earnings exhibit or a registration statement.
ANNUAL_REPORT_FORMS = ("10-K", "20-F", "40-F")

US_GAAP = "us-gaap"
DEI = "dei"


# --------------------------------------------------------------------------- #
# Concept vocabulary
#
# Ordered by preference. These are alias lists, not synonyms: the first entry is
# the concept that most precisely means the thing we want, later entries are
# what filers use when they do not tag the first.
# --------------------------------------------------------------------------- #

REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "SalesRevenueServicesNet",
    # Banks and insurers report revenue net of interest expense.
    "RevenuesNetOfInterestExpense",
)

COST_OF_REVENUE = (
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
    "CostOfServices",
)

GROSS_PROFIT = ("GrossProfit",)

OPERATING_INCOME = (
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
)

NET_INCOME = (
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
)

# Almost always reported as a positive magnitude of an expense. Sign is
# normalised by the caller, never assumed here.
INTEREST_EXPENSE = (
    "InterestExpense",
    "InterestExpenseNonoperating",
    "InterestExpenseDebt",
    "InterestAndDebtExpense",
    "InterestExpenseBorrowings",
    # The 2024 US-GAAP taxonomy deprecated the plain `InterestExpense` element
    # in favour of an operating/nonoperating split, so filers that used to tag
    # the former simply stop: Realty Income's series goes dark after FY2023
    # without this. Last in priority because for a deposit-taking bank this
    # concept is interest paid to depositors, which is a cost of revenue rather
    # than debt service.
    "InterestExpenseOperating",
)

OPERATING_CASH_FLOW = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)

CAPEX = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForCapitalImprovements",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
)

CASH = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)

SHORT_TERM_INVESTMENTS = (
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    "OtherShortTermInvestments",
    # Last resort. NVIDIA puts no current/noncurrent split on its securities at
    # all — only the undifferentiated `AvailableForSaleSecuritiesDebtSecurities`
    # and this maturity schedule. The schedule is the safe half of it: debt
    # securities maturing within a year are short-term by definition, and taking
    # it instead of the undifferentiated total leaves the multi-year tranche out
    # rather than counting illiquid holdings as liquidity.
    "AvailableForSaleSecuritiesDebtMaturitiesWithinOneYearFairValue",
)

# Noncurrent portion only. `...IncludingCurrentMaturities` is deliberately
# absent: it is also tagged by many filers and adding it to the current-debt
# concepts below would count the current maturities twice.
LONG_TERM_DEBT = (
    "LongTermDebtNoncurrent",
    # Coca-Cola and other pre-ASC 842 style filers fold capital leases in and
    # tag the combined figure with no `Noncurrent` suffix.
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "LongTermDebt",
    # REITs present an unclassified balance sheet — no current/noncurrent split
    # at all — and carry their borrowings as "notes and bonds payable, net".
    # Realty Income tags $25bn here and nothing under any Debt concept, so
    # without these its whole balance-sheet stage reports UNKNOWN.
    "NotesPayable",
    "SecuredDebt",
)

SHORT_TERM_DEBT = (
    "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "DebtCurrent",
    "ShortTermBorrowings",
    "OtherShortTermBorrowings",
)

STOCKHOLDERS_EQUITY = (
    # Parent-only equity: the correct denominator for ROE attributable to
    # common shareholders.
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)

# Needed to derive total liabilities for the many filers that never tag it —
# Coca-Cola among them. Assets - equity(incl. NCI) is an identity, so the
# derived figure is exact, not an estimate.
TOTAL_EQUITY_INCL_NCI = (
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "StockholdersEquity",
)

TOTAL_LIABILITIES = ("Liabilities",)
TOTAL_ASSETS = ("Assets",)

DILUTED_SHARES = (
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfDilutedSharesOutstandingAdjustment",
)
BASIC_SHARES = ("WeightedAverageNumberOfSharesOutstandingBasic",)

DILUTED_EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")

# Cover-page share count — an instant, and the closest thing to "shares
# outstanding right now" for a market cap.
SHARES_OUTSTANDING_DEI = ("EntityCommonStockSharesOutstanding",)


# --------------------------------------------------------------------------- #
# Fact model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fact:
    concept: str
    start: date | None
    end: date
    value: Decimal
    unit: str
    accn: str
    form: str
    filed: date

    @property
    def duration_days(self) -> int | None:
        if self.start is None:
            return None
        return (self.end - self.start).days

    @property
    def is_annual_duration(self) -> bool:
        days = self.duration_days
        return days is not None and ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


@dataclass(frozen=True)
class Observation:
    """One resolved value for one fiscal period, with its provenance.

    Provenance is carried all the way to the API because a research tool whose
    numbers cannot be traced back to a filing is just a vendor API with extra
    steps.
    """

    value: Decimal
    concept: str
    accn: str
    form: str
    filed: date
    period_end: date


@dataclass
class Series:
    """A concept resolved across the canonical fiscal calendar."""

    primary_concept: str | None = None
    # Every concept that contributed. More than one means the series is stitched
    # across a taxonomy change and the UI should say so.
    concepts_used: list[str] = field(default_factory=list)
    by_period_end: dict[date, Observation] = field(default_factory=dict)

    def get(self, period_end: date) -> Decimal | None:
        obs = self.by_period_end.get(period_end)
        return None if obs is None else obs.value

    def observation(self, period_end: date) -> Observation | None:
        return self.by_period_end.get(period_end)

    @property
    def is_stitched(self) -> bool:
        return len(self.concepts_used) > 1


@dataclass(frozen=True)
class FiscalPeriod:
    """One annual reporting period on the filer's own calendar."""

    end: date
    start: date | None
    label: int

    @property
    def display(self) -> str:
        return f"FY{self.label}"


def fiscal_label(end: date) -> int:
    """The fiscal year a period ending on `end` is called by its filer.

    A 52/53-week filer on a December calendar can end on 1-3 January and that is
    the *prior* fiscal year. A 31 January filer (NVIDIA, Walmart, Salesforce)
    names the year by its ending calendar year — NVIDIA's year ended
    2026-01-25 is fiscal 2026. The early-January cutoff separates the two cases.
    """
    if end.month == 1 and end.day <= 14:
        return end.year - 1
    return end.year


def to_cents(value: Decimal) -> int:
    """USD -> integer cents, the only money representation in this project."""
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# CompanyFacts
# --------------------------------------------------------------------------- #


class CompanyFacts:
    """Query interface over one filer's `companyfacts` payload."""

    def __init__(self, payload: dict[str, Any]):
        self._facts = payload.get("facts", {})
        self.entity_name: str = payload.get("entityName", "") or ""
        self.cik: int = int(payload.get("cik", 0) or 0)
        self._periods: list[FiscalPeriod] | None = None

    # -- raw access -------------------------------------------------------- #

    def _raw_facts(self, concept: str, unit: str, taxonomy: str) -> list[Fact]:
        node = self._facts.get(taxonomy, {}).get(concept)
        if not node:
            return []
        rows = node.get("units", {}).get(unit)
        if not rows:
            return []

        out: list[Fact] = []
        for row in rows:
            try:
                end = date.fromisoformat(row["end"])
                filed = date.fromisoformat(row["filed"])
                start_raw = row.get("start")
                # Decimal(str(...)) rather than Decimal(float): the JSON values
                # are IEEE doubles and per-share amounts are not exact in binary.
                value = Decimal(str(row["val"]))
            except (KeyError, TypeError, ValueError):
                continue
            out.append(
                Fact(
                    concept=concept,
                    start=date.fromisoformat(start_raw) if start_raw else None,
                    end=end,
                    value=value,
                    unit=unit,
                    accn=str(row.get("accn", "")),
                    form=str(row.get("form", "")),
                    filed=filed,
                )
            )
        return out

    def has_concept(self, concept: str, taxonomy: str = US_GAAP) -> bool:
        return concept in self._facts.get(taxonomy, {})

    # -- fiscal calendar --------------------------------------------------- #

    def periods(self) -> list[FiscalPeriod]:
        """The filer's annual periods, oldest first.

        Anchored on the concepts every operating company reports — revenue, net
        income, operating cash flow — so the calendar exists even when one of
        them is tagged unusually. Built once and reused, because every other
        series aligns to it.
        """
        if self._periods is not None:
            return self._periods

        anchors: list[Fact] = []
        for aliases in (REVENUE, NET_INCOME, OPERATING_CASH_FLOW):
            for concept in aliases:
                anchors.extend(
                    f
                    for f in self._raw_facts(concept, "USD", US_GAAP)
                    if f.is_annual_duration
                )

        if not anchors:
            self._periods = []
            return self._periods

        # Cluster period ends that are within tolerance of each other: the same
        # fiscal year re-reported, possibly with 52/53-week drift.
        ends = sorted({f.end for f in anchors})
        clusters: list[list[date]] = [[ends[0]]]
        for end in ends[1:]:
            if (end - clusters[-1][-1]).days <= PERIOD_TOLERANCE_DAYS:
                clusters[-1].append(end)
            else:
                clusters.append([end])

        periods: list[FiscalPeriod] = []
        for cluster in clusters:
            # The representative end is the one the most recent filing used, so
            # the canonical date matches how the company reports today.
            in_cluster = [f for f in anchors if f.end in cluster]
            newest = max(in_cluster, key=lambda f: (f.filed, f.accn))
            starts = [f.start for f in in_cluster if f.start is not None]
            periods.append(
                FiscalPeriod(
                    end=newest.end,
                    start=min(starts) if starts else None,
                    label=fiscal_label(newest.end),
                )
            )

        # Two clusters can land on the same label when a filer changes its
        # fiscal year end; keep the later period so the series stays strictly
        # increasing and de-duplicated by label.
        deduped: dict[int, FiscalPeriod] = {}
        for period in periods:
            existing = deduped.get(period.label)
            if existing is None or period.end > existing.end:
                deduped[period.label] = period

        self._periods = sorted(deduped.values(), key=lambda p: p.end)
        return self._periods

    def recent_periods(self, count: int) -> list[FiscalPeriod]:
        """The `count` most recent annual periods, oldest first."""
        return self.periods()[-count:] if count > 0 else []

    # -- series resolution ------------------------------------------------- #

    def _resolve(
        self,
        aliases: Sequence[str],
        periods: Sequence[FiscalPeriod],
        *,
        unit: str,
        instant: bool,
        taxonomy: str = US_GAAP,
    ) -> Series:
        """Align an alias list onto the canonical calendar.

        One primary concept is chosen — whichever covers the most periods, ties
        broken by alias priority — and lower-priority aliases only fill the
        periods it misses. See trap 4 in the module docstring.
        """
        series = Series()
        if not periods:
            return series

        # candidates[concept][period_end] = best Fact for that cell
        candidates: dict[str, dict[date, Fact]] = {}

        for concept in aliases:
            facts = self._raw_facts(concept, unit, taxonomy)
            if not facts:
                continue
            selected: dict[date, Fact] = {}
            for period in periods:
                matches = [
                    f
                    for f in facts
                    if abs((f.end - period.end).days) <= PERIOD_TOLERANCE_DAYS
                    and (f.start is None if instant else f.is_annual_duration)
                ]
                if not matches:
                    continue
                # Audited annual report first, then the most recent filing —
                # so a restatement supersedes the original (trap 3).
                best = max(
                    matches,
                    key=lambda f: (
                        f.form.upper().startswith(ANNUAL_REPORT_FORMS),
                        f.filed,
                        f.accn,
                    ),
                )
                selected[period.end] = best
            if selected:
                candidates[concept] = selected

        if not candidates:
            return series

        priority = {concept: index for index, concept in enumerate(aliases)}
        primary = min(
            candidates, key=lambda c: (-len(candidates[c]), priority[c])
        )
        series.primary_concept = primary

        used: list[str] = []
        for period in periods:
            fact = candidates[primary].get(period.end)
            if fact is None:
                for concept in aliases:
                    if concept == primary:
                        continue
                    fact = candidates.get(concept, {}).get(period.end)
                    if fact is not None:
                        break
            if fact is None:
                continue
            if fact.concept not in used:
                used.append(fact.concept)
            series.by_period_end[period.end] = Observation(
                value=fact.value,
                concept=fact.concept,
                accn=fact.accn,
                form=fact.form,
                filed=fact.filed,
                period_end=period.end,
            )

        series.concepts_used = used
        return series

    def duration_series(
        self,
        aliases: Sequence[str],
        periods: Sequence[FiscalPeriod],
        unit: str = "USD",
    ) -> Series:
        """Flow concepts — revenue, income, cash flow — over each annual period."""
        return self._resolve(aliases, periods, unit=unit, instant=False)

    def instant_series(
        self,
        aliases: Sequence[str],
        periods: Sequence[FiscalPeriod],
        unit: str = "USD",
        taxonomy: str = US_GAAP,
    ) -> Series:
        """Balance-sheet concepts, measured at each annual period end."""
        return self._resolve(
            aliases, periods, unit=unit, instant=True, taxonomy=taxonomy
        )

    def latest_instant(
        self, aliases: Sequence[str], unit: str, taxonomy: str = US_GAAP
    ) -> Observation | None:
        """Most recently *filed* instant, ignoring the fiscal calendar.

        Used for the cover-page share count, which is dated a few weeks after
        the period end and therefore never aligns to it.
        """
        best: Fact | None = None
        for concept in aliases:
            for fact in self._raw_facts(concept, unit, taxonomy):
                if fact.start is not None:
                    continue
                if best is None or (fact.end, fact.filed) > (best.end, best.filed):
                    best = fact
        if best is None:
            return None
        return Observation(
            value=best.value,
            concept=best.concept,
            accn=best.accn,
            form=best.form,
            filed=best.filed,
            period_end=best.end,
        )


# --------------------------------------------------------------------------- #
# Derived arithmetic
# --------------------------------------------------------------------------- #


def cagr_bps(first: Decimal, last: Decimal, years: int) -> int | None:
    """Compound annual growth rate in basis points.

    `None` when undefined rather than a number that reads as a rate: a CAGR from
    a zero or negative base is not a small growth rate, it is not a growth rate
    at all, and returning 0 or a large negative would be read as a real result.
    """
    if years <= 0 or first <= 0 or last <= 0:
        return None
    ratio = float(last) / float(first)
    rate = ratio ** (1.0 / years) - 1.0
    return round(rate * 10_000)


def ratio_bps(numerator: Decimal | None, denominator: Decimal | None) -> int | None:
    """`numerator / denominator` in basis points, or None if not computable."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(float(numerator) / float(denominator) * 10_000)


def trend_direction(values: Sequence[Decimal | int | None]) -> str:
    """Classify a short series as EXPANDING / STABLE / CONTRACTING / MIXED.

    Compares the mean of the first half against the mean of the second half
    rather than just the endpoints, so a single anomalous year — a COVID
    quarter, a one-off charge — does not decide the direction of a five-year
    trend. `MIXED` is reserved for a series that moved materially in both
    directions, which is a genuinely different message from "flat".
    """
    clean = [Decimal(v) for v in values if v is not None]
    if len(clean) < 2:
        return "UNKNOWN"

    midpoint = len(clean) // 2
    early = clean[:midpoint] or clean[:1]
    late = clean[midpoint:] or clean[-1:]
    early_mean = sum(early) / len(early)
    late_mean = sum(late) / len(late)

    if early_mean == 0:
        return "UNKNOWN"
    change = (late_mean - early_mean) / abs(early_mean)

    steps = [clean[i + 1] - clean[i] for i in range(len(clean) - 1)]
    ups = sum(1 for s in steps if s > 0)
    downs = sum(1 for s in steps if s < 0)

    # Under 2% drift over the window is noise, not a trend.
    if abs(change) < Decimal("0.02"):
        return "MIXED" if ups and downs else "STABLE"
    if change > 0:
        return "EXPANDING" if downs <= ups else "MIXED"
    return "CONTRACTING" if ups <= downs else "MIXED"


def series_values(series: Series, periods: Iterable[FiscalPeriod]) -> list[Decimal | None]:
    return [series.get(p.end) for p in periods]
