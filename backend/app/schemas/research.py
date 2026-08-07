"""Wire format for the stock research engine.

Two conventions carry over from the rest of the platform and one is new.

Money stays in integer cents and ratios stay in basis points, so nothing that
came out of a filing is ever rounded on its way through JSON. P/E and PEG are
`Decimal` serialised as strings, for the same reason `quantity` is in the
investments schema: they are quotients, and a float round-trip is silent
corruption.

The new one is that **every check ships its own rendered display string**. The
backend already knows whether a value is a percentage, a multiple, a money pair
or the word "Negative equity", and it knows the caveat that belongs underneath
it. Re-deriving that in TypeScript would mean duplicating the framework's
judgment in a second language, where it would drift. The frontend renders
`value_display`, `target_display` and `detail` verbatim and decides only on
colour, which is a function of `status` alone.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer

# PASS | WARN | FAIL | UNKNOWN
Status = str


class EvidenceOut(BaseModel):
    """A quoted passage from the filing, so a heuristic verdict can be audited."""

    model_config = ConfigDict(from_attributes=True)

    text: str
    matched: str


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    status: Status
    value_display: str
    target_display: str
    detail: str
    value_bps: int | None = None
    value_cents: int | None = None


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    number: int
    title: str
    description: str
    status: Status
    checks: list[CheckOut]
    counts: dict[str, int]


class AnnualRowOut(BaseModel):
    """One fiscal year of the normalised series, for the charts and the table."""

    model_config = ConfigDict(from_attributes=True)

    fiscal_label: str
    period_end: date
    revenue_cents: int | None
    gross_profit_cents: int | None
    gross_margin_bps: int | None
    operating_income_cents: int | None
    operating_margin_bps: int | None
    net_income_cents: int | None
    net_margin_bps: int | None
    diluted_shares: int | None
    diluted_eps: Decimal | None
    operating_cash_flow_cents: int | None
    capex_cents: int | None
    free_cash_flow_cents: int | None
    cash_and_sti_cents: int | None
    total_debt_cents: int | None
    long_term_debt_cents: int | None
    equity_cents: int | None
    total_liabilities_cents: int | None
    interest_expense_cents: int | None

    @field_serializer("diluted_eps")
    def _eps(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value.normalize(), "f")


class BusinessModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    classification: str
    confidence: str
    recurring_score: int
    transactional_score: int
    evidence: list[EvidenceOut]


class CustomerConcentrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    confidence: str
    max_customer_bps: int | None
    threshold_bps: int
    evidence: list[EvidenceOut]


class InsiderOwnershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    group_bps: int | None
    group_person_count: int | None
    confidence: str
    evidence: list[EvidenceOut]


class PriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    price_cents: int
    source: str
    as_of: date | None
    is_stale_close: bool


class SourceFilingOut(BaseModel):
    """The primary documents the report was built from, linked for the reader."""

    model_config = ConfigDict(from_attributes=True)

    form: str
    filed: date
    period: date | None
    document_url: str
    index_url: str


class ResearchReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    cik: int
    company_name: str
    generated_at: date

    stages: list[StageOut]
    annuals: list[AnnualRowOut]

    business_model: BusinessModelOut | None
    concentration: CustomerConcentrationOut | None
    insider: InsiderOwnershipOut | None

    price: PriceOut | None
    market_cap_cents: int | None
    pe_ratio: Decimal | None
    peg_ratio: Decimal | None
    eps_growth_bps: int | None

    sources: list[SourceFilingOut]
    stitched_concepts: list[str]
    # Everything the reader should know before trusting a number: split
    # restatements, unreadable sections, sector caveats, stale prices.
    notes: list[str]
    summary_counts: dict[str, int]

    @field_serializer("pe_ratio", "peg_ratio")
    def _ratio(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value.normalize(), "f")


class TickerMatchOut(BaseModel):
    """One row of the search-as-you-type list on /research."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    cik: int
    name: str


class TickerSearchOut(BaseModel):
    query: str
    results: list[TickerMatchOut]
