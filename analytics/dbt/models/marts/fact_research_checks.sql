-- Grain: one row per company per snapshot date per stage per check.
--
-- This is the model that justifies landing the research data rather than
-- calling the API. `app/services/research.py` returns a verdict for right now
-- and keeps nothing; one row per check per DAY turns the same framework into a
-- history, so "which checks flipped, and when" becomes a query instead of an
-- impossibility.
--
-- `status_changed` and `previous_status` are computed here rather than left to
-- the consumer because the window is easy to get subtly wrong: the partition
-- has to be (ticker, stage_key, check_key), and partitioning by ticker alone —
-- the obvious mistake — would compare a leverage check against whatever check
-- happened to precede it and report a flip on almost every row.
--
-- No user_id: public filings. The join to a person is ticker → dim_securities.

with checks as (

    select * from {{ ref('stg_research_checks') }}

),

with_previous as (

    select
        *,
        lag(status) over (
            partition by ticker, stage_key, check_key
            order by snapshot_date
        )                                       as previous_status,
        lag(snapshot_date) over (
            partition by ticker, stage_key, check_key
            order by snapshot_date
        )                                       as previous_snapshot_date
    from checks

)

select
    {{ dbt_utils.generate_surrogate_key([
        'ticker', 'snapshot_date', 'stage_key', 'check_key'
    ]) }}                                       as research_check_key,

    ticker,
    snapshot_date,
    stage_key,
    stage_number,
    stage_title,
    stage_status,
    check_key,
    check_label,

    status,
    previous_status,
    previous_snapshot_date,

    value_bps,
    value_cents,

    is_adverse,
    is_answerable,
    passed_flag,

    -- A change of verdict. Null previous_status means this is the first
    -- observation, which is NOT a change — treating it as one would report
    -- every check as having just flipped on the day research first ran.
    previous_status is not null and status <> previous_status
                                                as status_changed,

    -- The two directions worth alerting on, separated. A check going PASS to
    -- FAIL is the signal; FAIL to PASS is a company fixing something and reads
    -- very differently in a feed.
    -- Both guarded on previous_status being present FIRST. `null in (...)`
    -- evaluates to null rather than false in SQL, so without the guard a
    -- check's first ever observation would produce a null flag — and a null
    -- boolean read as "not deteriorated" by one consumer and skipped entirely
    -- by another is precisely the kind of disagreement a mart exists to remove.
    previous_status is not null
        and previous_status not in ('WARN', 'FAIL')
        and status in ('WARN', 'FAIL')          as deteriorated,

    previous_status is not null
        and previous_status in ('WARN', 'FAIL')
        and status = 'PASS'                     as improved
from with_previous
