-- One row per researched company.
--
-- THIS IS THE CONFORMED DIMENSION, and it is the reason the research data is
-- landed at all.
--
-- `dim_companies.ticker` and `dim_securities.ticker_symbol` come from two
-- source systems that share nothing: Plaid assigns its own security ids per
-- item, the SEC assigns CIKs, and neither has heard of the other. Ticker is the
-- only value both emit for the same real-world entity, which makes it the
-- conformed key — and it is the join that lets a question like "what are the
-- fundamentals of the things I actually own" be answered at all.
--
-- `is_held` is computed here rather than left to the consumer because the
-- direction of that join is easy to get wrong. An inner join from securities to
-- companies silently drops every holding with no filings (an ETF, bitcoin, a
-- money-market sweep), which looks like a smaller portfolio rather than an
-- incomplete one. Materialising the flag makes the drop a filter someone chose.
--
-- No user_id. See analytics/ddl/landing.sql: these are public filings, the same
-- for everyone. The user-scoped side of the join is dim_securities.

with companies as (

    select * from {{ ref('stg_research_companies') }}

),

securities as (

    select * from {{ ref('stg_securities') }}

),

-- Aggregated to one row per ticker BEFORE the join. The same ticker can be
-- several `security` rows — Plaid issues a security id per item, so AAPL held
-- at two brokerages is two rows — and joining without this would multiply
-- dim_companies and break its uniqueness test.
holdings_by_ticker as (

    select
        ticker_symbol                       as ticker,
        count(*)                            as security_row_count,
        min(security_name)                  as portfolio_security_name
    from securities
    where ticker_symbol is not null
    group by ticker_symbol

),

fundamentals as (

    select
        ticker,
        count(*)                            as fiscal_year_count,
        min(fiscal_period_end)              as earliest_fiscal_period_end,
        max(fiscal_period_end)              as latest_fiscal_period_end
    from {{ ref('stg_research_fundamentals') }}
    group by ticker

)

select
    companies.ticker                                        as ticker,
    companies.cik,
    companies.company_name,

    -- The name Plaid gave the instrument, kept alongside the name the SEC gave
    -- the filer. They routinely disagree ("Apple Inc." vs "APPLE INC"), and
    -- keeping both is what makes a mismatch investigable instead of a silent
    -- overwrite of one system's truth by the other's.
    holdings_by_ticker.portfolio_security_name,

    companies.first_snapshot_date,
    companies.last_snapshot_date,
    companies.snapshot_count,
    companies.has_history,
    companies.days_since_snapshot,

    coalesce(fundamentals.fiscal_year_count, 0)             as fiscal_year_count,
    fundamentals.earliest_fiscal_period_end,
    fundamentals.latest_fiscal_period_end,

    -- Is this company something the portfolio actually holds?
    holdings_by_ticker.ticker is not null                   as is_held,
    coalesce(holdings_by_ticker.security_row_count, 0)      as held_security_row_count
from companies
left join holdings_by_ticker
    on companies.ticker = holdings_by_ticker.ticker
left join fundamentals
    on companies.ticker = fundamentals.ticker
