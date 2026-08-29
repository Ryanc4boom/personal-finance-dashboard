-- One row per transaction. The grain is the source row; nothing is aggregated
-- and nothing is filtered out.
--
-- Nothing is filtered out ON PURPOSE. It is tempting to drop pending rows, or
-- transfers, or excluded-from-budget rows here so that consumers "just get
-- spend". That would make this the only place those rows exist, silently
-- change every total, and make reconciliation against the source impossible.
-- The fact carries every row plus the flags needed to filter; the flags are
-- the product, not the filtering.
--
-- Not incremental. `transaction` rows are HARD DELETED on a Plaid `removed`
-- delta — services/ingestion.remove_transaction issues a real db.delete() and
-- there is no is_removed or deleted_at column. An incremental model has no way
-- to learn about a deletion and would retain rows that no longer exist,
-- drifting further from the source on every run. At this volume a full rebuild
-- is free, so incremental would buy nothing and cost correctness.

{% set unknown_key = '00000000-0000-0000-0000-000000000000' %}

with transactions as (

    select * from {{ ref('stg_transactions') }}

),

account_ownership as (

    select * from {{ ref('int_account_ownership') }}

),

merchants as (

    select merchant_id, canonical_merchant_id from {{ ref('stg_merchants') }}

),

categories as (

    select category_id, category_kind from {{ ref('stg_categories') }}

)

select
    transactions.transaction_id,

    -- Ownership. Resolved through int_account_ownership rather than re-walked
    -- here. Every fact in this project carries user_id for the reason given in
    -- that model: on single-user data a missing ownership filter is invisible.
    account_ownership.user_id,

    -- Foreign keys to the dimensions.
    transactions.account_id,
    -- Coalesced onto the unknown member so there are no NULL keys: an inner
    -- join to dim_categories then cannot silently drop uncategorised spend.
    coalesce(transactions.category_id, '{{ unknown_key }}'::uuid)   as category_id,
    -- Resolved to the CANONICAL merchant before the coalesce. Joining on the
    -- raw merchant_id would split one merchant's spend across its aliases.
    coalesce(merchants.canonical_merchant_id, '{{ unknown_key }}'::uuid)
                                                                    as merchant_id,

    -- Date foreign key, in the same YYYYMMDD integer form as dim_time.
    transactions.transaction_date,
    (to_char(transactions.transaction_date, 'YYYYMMDD'))::int        as date_key,
    transactions.posted_date,

    -- Measures. Integer cents throughout; no float ever touches money here.
    transactions.signed_amount_cents,
    transactions.abs_amount_cents,

    -- Degenerate dimensions — attributes of the transaction itself, with no
    -- dimension table of their own worth building.
    transactions.direction,
    transactions.category_source,
    transactions.provider_category,
    transactions.description_raw,
    transactions.provider_merchant_name,

    transactions.is_pending,
    transactions.is_transfer,
    transactions.is_recurring,
    transactions.excluded_from_budget,

    -- The derived flag consumers actually want. Spendable means: real spend
    -- that should count against a budget.
    --
    -- Deriving it once, here, is the point. Every consumer that needs "what did
    -- I actually spend" otherwise re-writes this predicate, and they will not
    -- all write the same one — forgetting is_transfer double-counts every
    -- internal movement between accounts as spend, which is precisely the
    -- error assert_transfer_pairs_net_to_zero exists to catch downstream.
    (
        not transactions.is_transfer
        and not transactions.excluded_from_budget
        and categories.category_kind = 'EXPENSE'
    )                                                                as is_spendable,

    -- Degenerate id: the other leg of an internal transfer. Kept on the fact
    -- rather than modelled as a dimension because a transfer pair has no
    -- attributes of its own — it is an identifier, not an entity.
    transactions.transfer_pair_id,
    transactions.recurring_stream_id,

    transactions.provider_txn_id,
    transactions.transaction_created_at,
    transactions.transaction_updated_at

from transactions
-- Inner join is correct here and only here: account_id is NOT NULL with a FK
-- to account, so a transaction with no resolvable account cannot exist. If one
-- ever does, losing it loudly (via the row-count reconciliation test) beats
-- carrying a fact row with no owner.
inner join account_ownership
    on transactions.account_id = account_ownership.account_id
left join merchants
    on transactions.merchant_id = merchants.merchant_id
left join categories
    on transactions.category_id = categories.category_id
