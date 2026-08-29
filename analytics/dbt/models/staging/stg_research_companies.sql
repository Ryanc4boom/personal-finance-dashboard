-- One row per company that has ever been researched.
--
-- `ticker` gets the same upper/trim/nullif treatment as
-- stg_securities.ticker_symbol, and for the same reason: it is the conformed
-- key between the SEC's world and Plaid's, and those two systems share no
-- identifier at all. If one side normalises and the other does not, the join
-- returns zero rows and reports it as "no research available" rather than as an
-- error. Both sides normalise identically, in staging, once.
--
-- `days_since_snapshot` is derived here rather than in the mart so every
-- downstream consumer answers "is this stale" the same way.

with src_research_companies as (

    select * from {{ source('research', 'research_company') }}

)

select
    nullif(upper(trim(ticker)), '')             as ticker,
    cik,
    company_name,
    last_snapshot_date,
    first_snapshot_date,
    snapshot_count,
    -- Distinct days the framework has been run against this company. A company
    -- seen exactly once has no trend yet, which is worth being able to filter
    -- on before drawing a line through a single point.
    snapshot_count > 1                          as has_history,
    (current_date - last_snapshot_date)         as days_since_snapshot,
    updated_at
from src_research_companies
where ticker is not null
