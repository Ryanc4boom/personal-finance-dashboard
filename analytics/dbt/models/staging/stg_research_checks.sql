-- One row per company per snapshot per check.
--
-- The status vocabulary is PASS / WARN / FAIL / UNKNOWN. UNKNOWN is not a
-- fifth wheel: it means the filing did not carry the concept the check needed,
-- which is a materially different statement from FAIL, and collapsing the two
-- would turn "we could not tell" into "this company is bad". `is_adverse`
-- below therefore covers WARN and FAIL only, and `is_answerable` exists so a
-- pass rate can be computed over the checks that actually had data.

with src_research_checks as (

    select * from {{ source('research', 'research_check') }}

)

select
    nullif(upper(trim(ticker)), '')      as ticker,
    snapshot_date,
    stage_key,
    check_key,
    stage_number,
    stage_title,
    stage_status,
    check_label,
    status,
    value_bps,
    value_cents,

    status in ('WARN', 'FAIL')           as is_adverse,
    status <> 'UNKNOWN'                  as is_answerable,
    -- Scored only where the check could be answered, so a company whose filing
    -- omitted half the concepts is not punished for it. Null rather than 0 for
    -- an UNKNOWN, so avg() skips it instead of dragging the mean down.
    case
        when status = 'UNKNOWN' then null
        when status = 'PASS'    then 1
        else 0
    end                                  as passed_flag,

    updated_at
from src_research_checks
where ticker is not null
