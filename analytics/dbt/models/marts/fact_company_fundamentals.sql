-- Grain: one row per company per fiscal year.
--
-- An ACCUMULATING fact in the sense that it holds the whole reported history
-- and a re-run overwrites a year rather than appending to it. That is a third
-- distinct grain in this warehouse, alongside fact_transactions (one row per
-- event) and fact_holdings (one row per entity per day), and the three of them
-- are the reason this is a star schema rather than a pile of tables.
--
-- Not additive across companies. Summing revenue_cents over dim_companies gives
-- a number with no referent — these are different entities in different
-- currencies-of-report reporting on different fiscal calendars. The measures
-- here are for trending WITHIN a company and comparing ratios ACROSS them, and
-- the year-over-year columns below exist so a consumer does not hand-roll a
-- window function and get the partition wrong.

with fundamentals as (

    select * from {{ ref('stg_research_fundamentals') }}

),

with_prior as (

    select
        *,
        -- Partition by ticker, order by period end. Ordering by fiscal_label
        -- would sort "FY2019" after "FY2018" by string luck and break the
        -- moment a filer labels a period anything else.
        lag(revenue_cents)     over (partition by ticker order by fiscal_period_end) as prior_revenue_cents,
        lag(net_income_cents)  over (partition by ticker order by fiscal_period_end) as prior_net_income_cents,
        lag(diluted_shares)    over (partition by ticker order by fiscal_period_end) as prior_diluted_shares
    from fundamentals

)

select
    {{ dbt_utils.generate_surrogate_key(['ticker', 'fiscal_period_end']) }}
                                                    as company_fundamental_key,
    ticker,
    cik,
    fiscal_period_end,
    fiscal_label,
    fiscal_year_end_year,
    snapshot_date,

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

    -- Cash flow
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
    has_negative_equity,

    -- Net cash position. Negative means the company owes more than it holds,
    -- which is the ordinary state for most filers and not a flag.
    case
        when cash_and_sti_cents is null or total_debt_cents is null then null
        else cash_and_sti_cents - total_debt_cents
    end                                             as net_cash_cents,

    -- Year-over-year growth in basis points.
    --
    -- Guarded on the PRIOR value being strictly positive, not merely non-zero.
    -- Growth from a negative base is arithmetically computable and completely
    -- meaningless: a company going from -$1M to -$500k in net income would
    -- report -50% growth while actually improving. Null is the honest answer,
    -- and it is what keeps a "fastest growing" ranking from being topped by a
    -- company whose losses shrank.
    case
        when prior_revenue_cents is null or prior_revenue_cents <= 0 then null
        else round(
            (revenue_cents - prior_revenue_cents) * 10000.0 / prior_revenue_cents
        )::int
    end                                             as revenue_growth_bps,

    case
        when prior_net_income_cents is null or prior_net_income_cents <= 0 then null
        else round(
            (net_income_cents - prior_net_income_cents) * 10000.0 / prior_net_income_cents
        )::int
    end                                             as net_income_growth_bps,

    -- Share count change. Positive means DILUTION — more shares outstanding, so
    -- each existing share owns less of the company. Named for the direction it
    -- actually measures, because "share growth" reads as good news and is not.
    case
        when prior_diluted_shares is null or prior_diluted_shares <= 0 then null
        else round(
            (diluted_shares - prior_diluted_shares) * 10000.0 / prior_diluted_shares
        )::int
    end                                             as dilution_bps,

    prior_revenue_cents is not null                 as has_prior_year
from with_prior
