-- Reconciles the Plaid delta log against the live transaction table.
--
-- This is the ONLY test here that can detect a hard delete.
--
-- services/ingestion.remove_transaction issues a real db.delete(). There is no
-- is_removed column, no deleted_at, no tombstone — a removed transaction leaves
-- no trace whatsoever in `transaction`. Every other test in this project reads
-- the current rows and therefore cannot tell the difference between "this row
-- was never ingested" and "this row was ingested and then vanished".
--
-- raw_transaction is the append-only audit log of every delta the app applied,
-- so (added - removed) is the count the ingestion pipeline BELIEVES it left
-- behind. Comparing that against reality catches:
--   * a delete that removed more rows than the delta asked for (a missing
--     ownership filter on the delete would do exactly this)
--   * an upsert that created duplicates instead of updating in place
--   * rows deleted out of band, by a manual query or a cascade nobody expected
--
-- This is also the test that makes the no-incremental-models decision
-- defensible rather than merely asserted — it is what would notice if a stale
-- row survived a rebuild.
--
-- SCOPED PER ITEM, not globally, and that scoping is load-bearing: the demo
-- seeds write `transaction` rows directly rather than driving them through
-- services/ingestion, so seeded items have no delta log at all. A global
-- comparison would report every seeded row as an unexplained surplus and the
-- test would be permanently red on the demo database — a test that is always
-- failing is a test everyone learns to ignore. Only items that HAVE a delta log
-- are reconciled; items ingested for real are exactly those items.
--
-- 'modified' events are ignored on purpose: a modification updates a row in
-- place and does not change the count.

with deltas as (

    select
        item_id,
        count(*) filter (where event_type = 'added')   as added_count,
        count(*) filter (where event_type = 'removed') as removed_count
    from {{ ref('stg_raw_transactions') }}
    group by item_id

),

live as (

    select
        account_ownership.item_id,
        count(*) as live_count
    from {{ ref('fact_transactions') }} as facts
    inner join {{ ref('int_account_ownership') }} as account_ownership
        on facts.account_id = account_ownership.account_id
    group by account_ownership.item_id

)

select
    deltas.item_id,
    deltas.added_count,
    deltas.removed_count,
    deltas.added_count - deltas.removed_count   as expected_count,
    coalesce(live.live_count, 0)                as live_count,
    coalesce(live.live_count, 0) - (deltas.added_count - deltas.removed_count)
                                                as discrepancy,
    'live transaction count does not match added minus removed in the delta log'
        as failure_reason
from deltas
left join live
    on deltas.item_id = live.item_id
where coalesce(live.live_count, 0) <> deltas.added_count - deltas.removed_count
