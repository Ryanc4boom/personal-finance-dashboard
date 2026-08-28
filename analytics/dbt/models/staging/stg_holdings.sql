-- Periodic snapshot: what each account held in each security, on each day.
--
-- The source is a TimescaleDB hypertable partitioned on as_of_date, which is
-- why its primary key is the composite (id, as_of_date) and NOT `id` alone.
-- A `unique` test on `id` here passes on day one and fails on day two. The
-- real business grain is (account_id, security_id, as_of_date) and that is
-- what gets tested.
--
-- `value_drift_cents` is a stored derived column: the gap between what the
-- institution reported as the position's value and what quantity × price
-- implies. It is re-derived and compared in
-- tests/assert_holding_value_reconciles.sql — validating a stored
-- denormalisation against its own inputs, which is the kind of thing no DB
-- constraint can express.

with src_holdings as (

    select * from {{ source('budgeting', 'holding') }}

)

select
    id                                  as holding_id,
    as_of_date,
    account_id,
    security_id,
    quantity,
    institution_price_cents,
    institution_value_cents,
    cost_basis_cents,
    value_drift_cents,
    -- NULL rather than 0 when there is no cost basis: an unknown gain is not
    -- a zero gain, and coalescing it to 0 would drag a portfolio-level average
    -- towards zero without anything looking wrong.
    institution_value_cents - cost_basis_cents  as unrealized_gain_cents,
    created_at                          as holding_created_at
from src_holdings
