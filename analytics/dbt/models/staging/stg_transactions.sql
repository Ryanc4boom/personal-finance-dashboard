-- One row per posted or pending transaction. This is the grain of the core fact.
--
-- Money stays in integer cents the whole way through. No ::numeric, no
-- /100.0, no rounding anywhere in this project — the app's convention is that
-- floats never touch money, and an analytics layer that quietly converts to
-- float is exactly how a reconciliation ends up off by a cent.
--
-- `amount_cents` IS ALREADY SIGNED at the source and is passed through
-- untouched. This is worth stating loudly because the obvious-looking
-- alternative is wrong: the first version of this model did
--
--     case direction when 'OUTFLOW' then -amount_cents ... end
--
-- on the assumption that the source stored a positive magnitude alongside a
-- direction flag. It does not. ck_transaction_direction_sign in
-- alembic/versions/0001_initial_schema.py reads
--
--     (direction = 'INFLOW'  AND amount_cents >= 0) OR
--     (direction = 'OUTFLOW' AND amount_cents <= 0)
--
-- so the sign is already there, and re-applying it turned every expense into
-- income. A spend-by-category chart built on that would have looked
-- completely plausible. Plaid's own convention is the inverse of this one and
-- is flipped exactly once, at ingestion — this layer must not flip it again.
-- The accepted_range test on amount_cents is what caught it; that is why
-- staging carries assumption tests and not just the marts.

with src_transactions as (

    select * from {{ source('budgeting', 'transaction') }}

)

select
    id                          as transaction_id,
    account_id,
    merchant_id,
    category_id,
    transfer_pair_id,
    recurring_stream_id,

    provider_txn_id,
    pending_provider_txn_id,

    date                        as transaction_date,
    posted_date,

    -- Signed: INFLOW positive, OUTFLOW negative. Sums directly to net flow.
    amount_cents                as signed_amount_cents,
    -- Magnitude, for "how much was this" questions where the direction is
    -- already carried by a filter or a grouping. Deriving it here stops every
    -- consumer from writing its own abs() and one of them forgetting.
    abs(amount_cents)           as abs_amount_cents,
    direction,

    description_raw,
    merchant_name               as provider_merchant_name,
    provider_category,
    category_source,

    is_pending,
    is_transfer,
    is_recurring,
    excluded_from_budget,
    tags,

    created_at                  as transaction_created_at,
    updated_at                  as transaction_updated_at
from src_transactions
