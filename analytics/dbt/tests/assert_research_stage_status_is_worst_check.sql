-- The stage rollup rule, re-derived in SQL and compared against what the
-- service actually said.
--
-- `Stage.status` in app/services/research.py is a derived property with two
-- clauses that are easy to get wrong and impossible to notice getting wrong:
--
--     worst verdict wins        — FAIL beats WARN beats PASS
--     and an UNKNOWN cannot be papered over by a PASS
--
-- That second clause is the subtle one. A stage where three checks PASS and one
-- could not be evaluated is UNKNOWN, not PASS, because the framework refuses to
-- call a company clean on evidence it never saw. Any reimplementation that
-- takes the max of the statuses, or that treats UNKNOWN as neutral, silently
-- upgrades those stages to PASS.
--
-- This is NOT tautological, in the same way assert_direction_sign_consistent is
-- not: the landing writer stamps the service's own stage.status onto every
-- check row, so the two sides of this comparison come from genuinely different
-- places — one from Python, one re-derived from the individual check verdicts
-- carried alongside it. A dbt model that reshaped or filtered the checks, or a
-- landing writer that grouped the wrong stage's status onto a row, breaks the
-- agreement and fails here.
--
-- Non-vacuous by construction: the demo portfolio's filers produce stages in
-- more than one status, verified by counting distinct stage_status before
-- trusting this test.

with checks as (

    select * from {{ ref('fact_research_checks') }}

),

derived as (

    select
        ticker,
        snapshot_date,
        stage_key,
        -- The stage status the checks imply. Ordered exactly as the dataclass
        -- orders it, worst first, with the UNKNOWN clause applied to the PASS
        -- branch only.
        case
            when bool_or(status = 'FAIL')    then 'FAIL'
            when bool_or(status = 'WARN')    then 'WARN'
            when bool_or(status = 'PASS')
                 and bool_or(status = 'UNKNOWN') then 'UNKNOWN'
            when bool_or(status = 'PASS')    then 'PASS'
            else 'UNKNOWN'
        end                                  as derived_stage_status,
        min(stage_status)                    as reported_stage_status,
        count(distinct stage_status)         as distinct_reported,
        count(*)                             as check_count
    from checks
    group by ticker, snapshot_date, stage_key

)

select
    ticker,
    snapshot_date,
    stage_key,
    check_count,
    derived_stage_status,
    reported_stage_status,
    case
        -- One stage cannot be in two states at once. If the landed rows for a
        -- single stage disagree about that stage's status, the writer stamped
        -- the wrong value and the comparison above is meaningless.
        when distinct_reported <> 1
            then 'stage rows disagree about the stage status'
        else 'stage status does not match the worst of its checks'
    end                                      as failure_reason
from derived
where distinct_reported <> 1
   or derived_stage_status <> reported_stage_status
