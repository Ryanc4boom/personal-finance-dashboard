-- The vacuous-green guard for the research half, and the only test that checks
-- the conformed dimension actually conforms.
--
-- The claim this whole subsystem rests on is that `dim_companies.ticker` and
-- `dim_securities.ticker_symbol` name the same real-world companies, so the two
-- source systems can be joined. Nothing else here tests that claim. Every other
-- research test is of the form "return rows that violate me", so with an empty
-- landing schema — SEC unreachable, the asset never run, a typo in the source
-- schema name — all of them pass, the build goes green, and the star schema
-- quietly has one arm missing.
--
-- Worse than empty is NON-empty but disjoint: if one side normalised ticker
-- case and the other did not, both tables fill up, every not_null and unique
-- test passes, and the join that is the entire point returns zero rows. That
-- failure is invisible to every other check in this project. It is exactly the
-- class of bug the staging layer's upper/trim/nullif exists to prevent, and
-- this is the test that proves the prevention works rather than assuming it.
--
-- THIS TEST COUPLES THE BUILD TO EDGAR AVAILABILITY, deliberately. It lives in
-- its own file rather than being folded into assert_facts_not_empty so that
-- coupling is visible: if a run must be able to succeed with SEC unreachable,
-- this is the one test to exclude, by name, as a decision someone made — not a
-- silent hole in a shared list.
--
-- Three claims, each with its own failure_reason so a red build says which:
--   1. companies were researched at all
--   2. fundamentals were landed for them
--   3. at least one researched company is one the portfolio actually holds

with companies as (

    select * from {{ ref('dim_companies') }}

),

fundamentals as (

    select * from {{ ref('fact_company_fundamentals') }}

),

-- Counted from the securities side independently, rather than reusing
-- dim_companies.is_held. is_held is itself computed by the join under test, so
-- reading it back would be circular: a broken join sets is_held false
-- everywhere and this test would confirm the breakage rather than catch it.
portfolio_tickers as (

    select distinct ticker_symbol as ticker
    from {{ ref('stg_securities') }}
    where ticker_symbol is not null

),

tallies as (

    select
        (select count(*) from companies)                     as company_count,
        (select count(*) from fundamentals)                  as fundamental_count,
        (select count(*) from portfolio_tickers)             as portfolio_ticker_count,
        (
            select count(*)
            from companies
            join portfolio_tickers using (ticker)
        )                                                    as conformed_count

),

claims as (

    select
        'companies_researched'      as claim,
        company_count               as observed,
        'no company was researched — every research test below this passes vacuously'
                                    as failure_reason
    from tallies
    where company_count = 0

    union all

    select
        'fundamentals_landed'       as claim,
        fundamental_count           as observed,
        'companies exist but no fiscal year was landed for any of them'
                                    as failure_reason
    from tallies
    where company_count > 0
      and fundamental_count = 0

    union all

    select
        'conformed_on_ticker'       as claim,
        conformed_count             as observed,
        'no researched ticker matches any held security — the conformed dimension joins to nothing'
                                    as failure_reason
    from tallies
    -- Only meaningful when both sides have rows. With an empty portfolio there
    -- is nothing to conform TO, and firing here would report a join failure
    -- when the real (and already reported) problem is upstream.
    where company_count > 0
      and portfolio_ticker_count > 0
      and conformed_count = 0

)

select * from claims
