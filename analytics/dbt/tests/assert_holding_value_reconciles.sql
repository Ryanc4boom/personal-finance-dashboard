-- Re-derives a STORED DENORMALISATION from its own inputs.
--
-- `holding.value_drift_cents` is written by the ingestion pipeline as
--
--     institution_value_cents - round(quantity * institution_price_cents)
--
-- and then never touched again. Nothing in the database enforces that it still
-- means that: it is a plain BigInteger column, so if the ingestion changes its
-- rounding, forgets to recompute the drift on a restated snapshot, or writes
-- the value for the wrong row, the column keeps a stale number and every
-- constraint, foreign key and not_null test in this project still passes. This
-- is the class of bug a schema cannot express — the value is individually
-- valid and jointly wrong.
--
-- The drift itself is NOT the failure. A non-zero drift is expected and
-- documented upstream: institutions round differently and some report a stale
-- price against a fresh value, which is precisely why the column exists rather
-- than the discrepancy being silently absorbed into a portfolio total. What
-- fails here is the stored number disagreeing with what its own inputs imply.
--
-- Tolerance is ±1 cent, and only ±1. quantity is Numeric(28,8) and the product
-- of a fractional share with an integer-cent price lands on a fraction of a
-- cent, so Postgres's rounding and Python's can legitimately differ by one in
-- the last place. Anything wider would make the test unable to see a real
-- rounding regression, which is most of what it is for.
--
-- Rows with a NULL institution_price_cents are excluded rather than treated as
-- zero: there is nothing to reconcile against, and coalescing the price to 0
-- would report the entire position value as drift on every one of them.

with holdings as (

    select * from {{ ref('stg_holdings') }}
    where institution_price_cents is not null

),

recomputed as (

    select
        holding_id,
        as_of_date,
        account_id,
        security_id,
        value_drift_cents                                   as stored_drift_cents,
        institution_value_cents
            - round(quantity * institution_price_cents)     as derived_drift_cents
    from holdings

)

select
    holding_id,
    as_of_date,
    account_id,
    security_id,
    stored_drift_cents,
    derived_drift_cents,
    'stored value_drift_cents disagrees with quantity x price by more than a rounding unit'
        as failure_reason
from recomputed
where abs(stored_drift_cents - derived_drift_cents) > 1
