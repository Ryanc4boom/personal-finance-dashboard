-- One row per company per fiscal year, straight off the landed XBRL series.
--
-- Nothing is recomputed here. Every margin and every free-cash-flow figure is
-- passed through exactly as `app/services/research.py` derived it, because
-- re-deriving them in SQL would replace the service's judgment — which concept
-- to stitch, how to renormalise a split, when a filer's tagging is unusable —
-- with a division. The point of landing the service's output is to keep that
-- judgment, and a dbt test then re-derives the margins to check the two agree
-- (see tests/assert_fundamental_margins_reconcile.sql).
--
-- Money is integer cents and ratios are basis points, matching the OLTP
-- convention. diluted_eps stays numeric: it is a quotient and a float
-- round-trip is silent corruption.

with src_research_fundamentals as (

    select * from {{ source('research', 'research_fundamentals') }}

)

select
    nullif(upper(trim(ticker)), '')                  as ticker,
    fiscal_period_end,
    fiscal_label,
    snapshot_date,
    cik,

    -- Income statement
    revenue_cents,
    gross_profit_cents,
    gross_margin_bps,
    operating_income_cents,
    operating_margin_bps,
    net_income_cents,
    net_margin_bps,
    diluted_shares,
    diluted_eps,

    -- Cash flow. capex is stored as an ABSOLUTE value by the service
    -- (`_cents(abs(capex))`), so free cash flow is ocf MINUS capex here, not
    -- plus. Getting that backwards is the classic sign error in this table and
    -- it is why assert_fundamental_fcf_reconciles exists.
    operating_cash_flow_cents,
    capex_cents,
    free_cash_flow_cents,

    -- Balance sheet
    cash_and_sti_cents,
    total_debt_cents,
    long_term_debt_cents,
    equity_cents,
    total_liabilities_cents,
    interest_expense_cents,

    -- Negative equity is a real state (buyback-heavy filers reach it), not a
    -- data error, and several downstream ratios are undefined when it happens.
    -- Flagging it once here keeps every consumer from rediscovering that a
    -- debt-to-equity ratio can come out negative and meaningless.
    equity_cents is not null and equity_cents < 0    as has_negative_equity,

    extract(year from fiscal_period_end)::int        as fiscal_year_end_year,
    updated_at
from src_research_fundamentals
where ticker is not null
