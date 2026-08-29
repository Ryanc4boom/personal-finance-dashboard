-- Stored derived figures, re-derived from their own inputs.
--
-- Same shape as assert_holding_value_reconciles: the source system carries both
-- the components and the result, so the result can be checked rather than
-- trusted. Three derivations, all of which `app/services/research.py` computes
-- once during XBRL normalisation and never revisits:
--
--   gross margin      = gross_profit / revenue
--   operating margin  = operating_income / revenue
--   free cash flow    = operating_cash_flow - capex
--
-- The free-cash-flow one is the important one. The service stores capex as an
-- ABSOLUTE value (`_cents(abs(capex))`) and subtracts it, but XBRL filers tag
-- capital expenditure with either sign depending on the taxonomy element, so
-- "is it plus or minus here" is a live question every time this column is
-- touched. A model that added capex instead of subtracting it would roughly
-- double free cash flow for a capital-intensive filer and still look entirely
-- plausible on a chart.
--
-- TOLERANCE, and why it is not zero.
--
-- The service converts each figure to cents independently with ROUND_HALF_UP,
-- so `_cents(ocf) - _cents(capex)` and `_cents(ocf - capex)` can legitimately
-- differ by one cent. The margins are basis points rounded from a quotient, so
-- they can be off by one in the last place. Demanding exactness would produce a
-- test that fails on arithmetic that is in fact correct — which trains people
-- to ignore it, and a test everyone ignores is worse than no test.
--
-- Rows where an input is NULL are excluded rather than treated as zero: a filer
-- that did not tag gross profit has no gross margin to check, and coalescing
-- would invent a 0% margin and then fail on it.

{% set bps_tolerance = 2 %}
{% set cents_tolerance = 1 %}

with fundamentals as (

    select * from {{ ref('fact_company_fundamentals') }}

),

gross_margin as (

    select
        ticker,
        fiscal_period_end,
        'gross_margin_bps'                          as measure,
        gross_margin_bps                            as reported,
        round(gross_profit_cents * 10000.0 / revenue_cents)::bigint as recomputed
    from fundamentals
    where gross_margin_bps  is not null
      and gross_profit_cents is not null
      and revenue_cents      is not null
      -- Guarded rather than assumed non-zero: a filer with no tagged revenue
      -- would divide by zero and error the test instead of failing it, which
      -- reads as a broken pipeline rather than a broken number.
      and revenue_cents <> 0

),

operating_margin as (

    select
        ticker,
        fiscal_period_end,
        'operating_margin_bps'                      as measure,
        operating_margin_bps                        as reported,
        round(operating_income_cents * 10000.0 / revenue_cents)::bigint as recomputed
    from fundamentals
    where operating_margin_bps   is not null
      and operating_income_cents is not null
      and revenue_cents          is not null
      and revenue_cents <> 0

),

free_cash_flow as (

    select
        ticker,
        fiscal_period_end,
        'free_cash_flow_cents'                      as measure,
        free_cash_flow_cents                        as reported,
        (operating_cash_flow_cents - capex_cents)::bigint as recomputed
    from fundamentals
    where free_cash_flow_cents      is not null
      and operating_cash_flow_cents is not null
      and capex_cents               is not null

),

combined as (

    select *, {{ bps_tolerance }}   as tolerance from gross_margin
    union all
    select *, {{ bps_tolerance }}   as tolerance from operating_margin
    union all
    select *, {{ cents_tolerance }} as tolerance from free_cash_flow

)

select
    ticker,
    fiscal_period_end,
    measure,
    reported,
    recomputed,
    abs(reported - recomputed)  as drift,
    tolerance,
    'stored value does not reconcile with its own inputs' as failure_reason
from combined
where abs(reported - recomputed) > tolerance
