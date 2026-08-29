-- Bridge table: one row per (transaction, tag).
--
-- `transaction.tags` is a text[] on the source row, which is a many-valued
-- attribute at the transaction grain. It cannot live on fact_transactions as
-- an array without breaking the star schema in two ways:
--
--   * an array column takes no accepted_values test and no relationships test,
--     so the one attribute users type by hand is the one nothing validates;
--   * every consumer has to unnest it themselves, and a join through an
--     unnested array is exactly where fan-out double-counting starts.
--
-- Splitting it out makes the fan-out explicit — anyone joining this to the
-- fact can see they are about to multiply rows — and gives tags a grain that
-- can actually be tested.
--
-- Array ORDER IS SIGNIFICANT in the source and is preserved as `tag_position`.
-- That is also why the array must never enter a surrogate key: two
-- transactions with the same tags in a different order would hash differently
-- and look like different rows.
--
-- Empty arrays produce no rows here, which is correct: an untagged transaction
-- has no tags, it does not have one NULL tag.

with transactions as (

    select
        transaction_id,
        account_id,
        tags
    from {{ ref('stg_transactions') }}
    where tags is not null
      and array_length(tags, 1) > 0

),

account_ownership as (

    select account_id, user_id from {{ ref('int_account_ownership') }}

),

exploded as (

    select
        transactions.transaction_id,
        transactions.account_id,
        tag.value       as tag,
        tag.ordinality  as tag_position
    from transactions
    cross join lateral unnest(transactions.tags) with ordinality as tag(value, ordinality)

)

select
    exploded.transaction_id,
    account_ownership.user_id,
    exploded.tag,
    exploded.tag_position,
    -- Case-folded for grouping. Users type "Vacation" and "vacation" and mean
    -- the same thing; grouping on the raw value reports them as two tags.
    lower(trim(exploded.tag))   as tag_normalized
from exploded
inner join account_ownership
    on exploded.account_id = account_ownership.account_id
