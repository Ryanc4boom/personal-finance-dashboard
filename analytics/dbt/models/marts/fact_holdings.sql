-- PERIODIC SNAPSHOT fact. One row per (account, security, as_of_date).
--
-- This is a different species from fact_transactions and the difference is the
-- thing to get right. fact_transactions is a transaction fact: every measure on
-- it is fully additive, so `sum(signed_amount_cents)` is meaningful across any
-- combination of dimensions including time. Nothing here is.
--
-- **The measures are SEMI-ADDITIVE.** They sum across accounts and securities
-- WITHIN one as_of_date, and summing them ACROSS dates is meaningless: the same
-- position is re-stated every single day, so a naive
--
--     select sum(market_value_cents) from fact_holdings
--
-- over a year of snapshots reports a portfolio roughly 365× too large. It does
-- not error, it does not look obviously wrong on a chart, and it is the single
-- most common way a snapshot fact is misread. The mitigations here are, in
-- order of how much they actually help:
--
--   1. `is_latest_snapshot`, so the common question ("what do I hold now")
--      is a flag filter rather than a correlated subquery someone writes wrong.
--   2. The `_cents` measures being named for a point in time (market_value,
--      cost_basis) rather than for a flow (spend, contribution).
--   3. assert_holdings_snapshot_is_semi_additive, which proves the grain holds
--      so at least the duplication is one-row-per-day and not worse.
--
-- Not incremental, for the same reason as fact_transactions: a re-sync can
-- restate an existing snapshot day (an institution correcting a price), and an
-- append-only incremental would keep both the old and corrected row and break
-- the grain. At this volume a full rebuild costs nothing.

with holdings as (

    select * from {{ ref('stg_holdings') }}

),

account_ownership as (

    select * from {{ ref('int_account_ownership') }}

),

-- Latest snapshot date PER ACCOUNT, not one global max across the warehouse.
--
-- This distinction matters more than it looks. Institutions sync on their own
-- schedules and one of them lagging by a day is routine, not exceptional. A
-- global `max(as_of_date)` would silently drop every position in the lagging
-- account from "current holdings", so the portfolio total quietly loses an
-- entire brokerage — a number that is wrong by a lot while looking perfectly
-- plausible, and which nothing else here would catch.
latest_per_account as (

    select
        account_id,
        max(as_of_date) as latest_as_of_date
    from holdings
    group by account_id

)

select
    -- Surrogate key on the real grain. `holding_id` alone is NOT unique: the
    -- source is a Timescale hypertable keyed (id, as_of_date), so the same id
    -- recurs on every partition. Anything downstream that needs a single-column
    -- unique key has to use this one.
    {{ dbt_utils.generate_surrogate_key([
        'holdings.account_id',
        'holdings.security_id',
        'holdings.as_of_date',
    ]) }}                                                       as holding_key,

    holdings.holding_id,

    -- Ownership, resolved through int_account_ownership rather than re-walked.
    account_ownership.user_id,

    -- Dimension foreign keys.
    holdings.account_id,
    holdings.security_id,
    holdings.as_of_date,
    (to_char(holdings.as_of_date, 'YYYYMMDD'))::int             as date_key,

    -- Measures. Semi-additive: see the header.
    --
    -- Numeric, not float. A fractional share is routine (0.00381 BTC) and float
    -- rounding would drift a portfolio total in the last cents, which is the
    -- same reason money is integer cents everywhere in this repo.
    holdings.quantity,

    -- The institution's own valuation is AUTHORITATIVE, deliberately, even
    -- where it disagrees with quantity × price. It is the number printed on the
    -- user's statement, and a warehouse that recomputes it produces totals the
    -- user cannot reconcile against their brokerage — which reads as the
    -- warehouse being broken regardless of which one is arithmetically nicer.
    holdings.institution_value_cents                            as market_value_cents,
    holdings.institution_price_cents                            as price_cents,

    -- Nullable and left that way. A transferred-in position frequently has no
    -- basis (brokerages lose it across an ACATS), and coalescing to 0 would
    -- report the whole position as gain — wrong, and wrong in the direction
    -- that ends up on a tax return.
    holdings.cost_basis_cents,
    holdings.unrealized_gain_cents,

    -- The stored disagreement between the reported value and quantity × price.
    -- Carried onto the fact rather than dropped at staging so the discrepancy
    -- is queryable; assert_holding_value_reconciles re-derives it.
    holdings.value_drift_cents,

    -- Signed-position flag. A short is rare but expressible, and a consumer
    -- assuming quantity > 0 gets an allocation chart with a negative slice.
    holdings.quantity < 0                                       as is_short_position,

    -- The filter that makes "what do I hold right now" a one-liner. Per
    -- account: see latest_per_account above for why global max is a trap.
    holdings.as_of_date = latest_per_account.latest_as_of_date  as is_latest_snapshot,

    holdings.holding_created_at

from holdings
-- Inner join, matching fact_transactions: account_id is NOT NULL with a FK to
-- account, so a holding with no resolvable owner cannot exist. If one somehow
-- does, losing it loudly beats carrying a position that belongs to nobody.
inner join account_ownership
    on holdings.account_id = account_ownership.account_id
inner join latest_per_account
    on holdings.account_id = latest_per_account.account_id
