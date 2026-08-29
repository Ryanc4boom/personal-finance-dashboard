-- THE business-rule test in this project.
--
-- An internal transfer between two of the user's own accounts is stored as TWO
-- transaction rows — money leaving one account and arriving in another — linked
-- by transfer_pair_id on both legs. Moving £500 from checking to savings makes
-- the user neither richer nor poorer, so the two legs must sum to exactly zero.
--
-- Why this matters, and why it is the test worth explaining:
--
--   * If a pair is broken — one leg mis-signed, one leg's partner deleted, one
--     leg never matched — the money movement is counted as real spend or real
--     income. A £500 transfer becomes £500 of "spending" in whatever category
--     the outbound leg landed in, and the monthly total is wrong by an amount
--     large enough to change a decision.
--   * NOTHING enforces this. There is no CHECK constraint that can express it
--     (it spans two rows), no NOT NULL that implies it, and no foreign key that
--     would notice. The pairing is done in application code, so a bug there is
--     invisible to the database.
--   * It fails LOUDLY and specifically. Unlike a row count or a freshness
--     check, a failure here names the exact pair and the exact non-zero
--     residual, which is enough to go and find the transaction.
--
-- Grain: one row per transfer pair. least/greatest normalises the two ids into
-- a stable pair key so each pair is evaluated once rather than once per leg.
--
-- The `having count(*) = 2` clause is a second assertion hiding in the first:
-- a pair with one leg (partner hard-deleted by a Plaid `removed` delta) or
-- three legs sums to something, possibly even zero, and would otherwise slip
-- through. Both shapes are reported.

with paired as (

    select
        least(transaction_id, transfer_pair_id)     as pair_key_low,
        greatest(transaction_id, transfer_pair_id)  as pair_key_high,
        user_id,
        signed_amount_cents
    from {{ ref('fact_transactions') }}
    where transfer_pair_id is not null

),

pair_totals as (

    select
        pair_key_low,
        pair_key_high,
        -- Cast through text because Postgres has no min() for uuid: uuid has
        -- the btree comparison operators that least/greatest above need, but
        -- no aggregate built on them. The cast is lexicographic, which is the
        -- same order uuid's own comparison uses, and this is only picking a
        -- representative value to report — nothing depends on which.
        min(user_id::text)::uuid    as user_id,
        -- A transfer pair spanning two users is not a rounding problem, it is
        -- an ownership breach: two people's ledgers linked by one id, with one
        -- user's money netted against another's. It is called out separately
        -- because the fix is nothing like the fix for a mis-signed leg, and
        -- because CLAUDE.md's rule 2 warns this class of bug is invisible
        -- while there is only one seeded user.
        count(distinct user_id)     as user_count,
        count(*)                    as leg_count,
        sum(signed_amount_cents)    as net_cents
    from paired
    group by pair_key_low, pair_key_high

)

select
    pair_key_low,
    pair_key_high,
    user_id,
    user_count,
    leg_count,
    net_cents,
    case
        when user_count <> 1 then 'transfer pair spans more than one user'
        when leg_count  <> 2 then 'pair does not have exactly two legs'
        else 'legs do not net to zero'
    end as failure_reason
from pair_totals
where net_cents <> 0
   or leg_count <> 2
   or user_count <> 1
