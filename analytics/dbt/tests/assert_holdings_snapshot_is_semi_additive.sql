-- Proves the periodic-snapshot grain of fact_holdings actually holds.
--
-- Two separate claims, both of which the model's consumers rely on and neither
-- of which any constraint downstream of the source enforces:
--
--   1. ONE row per (user, account, security, as_of_date). The unique test on
--      holding_key would catch a duplicate too, but it reports a hash — this
--      reports the business key that was duplicated, which is the difference
--      between a five-minute fix and an hour of unhashing.
--
--   2. EXACTLY ONE snapshot date per account is flagged is_latest_snapshot.
--      This is the load-bearing one. Every "what do I hold now" query is
--      `where is_latest_snapshot`, so if two dates were flagged for one
--      account, that account's positions would be counted twice and the
--      portfolio total would silently inflate by roughly one account. Zero
--      flagged is worse in a quieter way: the account disappears from current
--      holdings entirely and the total is short by exactly one brokerage,
--      which looks like a plausible number rather than an error.
--
-- Both failures produce a wrong total, never an error, and both survive every
-- not_null and relationships test in this project — which is the whole reason
-- to assert them explicitly.
--
-- Stated rather than hidden: in the demo fixture every account is snapshotted
-- on the same monthly calendar, so `is_latest_snapshot` currently resolves to
-- one shared date and claim 2 passes without the per-account branch of the
-- flag ever mattering. It is defensive against the real world, where a lagging
-- institution is routine, not something this data reproduces. Verified by
-- injecting a second flagged date, which the query above reports.

with duplicate_grain as (

    select
        account_id,
        security_id,
        as_of_date,
        count(*)                                as row_count,
        'duplicate rows at the snapshot grain (account, security, as_of_date)'
                                                as failure_reason
    from {{ ref('fact_holdings') }}
    group by account_id, security_id, as_of_date
    having count(*) > 1

),

latest_flag_per_account as (

    select
        account_id,
        count(distinct as_of_date)              as flagged_date_count
    from {{ ref('fact_holdings') }}
    where is_latest_snapshot
    group by account_id

),

bad_latest_flag as (

    select
        account_id,
        null::uuid                              as security_id,
        null::date                              as as_of_date,
        flagged_date_count                      as row_count,
        'account has ' || flagged_date_count || ' snapshot dates flagged is_latest_snapshot, expected 1'
                                                as failure_reason
    from latest_flag_per_account
    where flagged_date_count <> 1

)

select * from duplicate_grain
union all
select * from bad_latest_flag
