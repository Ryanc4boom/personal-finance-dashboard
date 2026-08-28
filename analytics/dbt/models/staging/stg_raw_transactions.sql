-- Append-only audit log of every Plaid delta the app has ever applied.
--
-- This model exists for exactly one purpose: reconciling
-- (added - removed) against the live `transaction` count. Because
-- services/ingestion.remove_transaction issues a real db.delete(), a removed
-- transaction leaves NO trace in `transaction` at all — this log is the only
-- record that it ever existed, and therefore the only thing that can detect a
-- hard delete. See tests/assert_transaction_count_reconciles.sql.
--
-- `raw_payload` (the full provider response, including account numbers and
-- masks) is not selected. It is not needed for the reconciliation and copying
-- it into a second schema would double the blast radius of a dump for no gain.

with src_raw_transactions as (

    select
        id,
        item_id,
        provider,
        provider_txn_id,
        event_type,
        received_at
    from {{ source('budgeting', 'raw_transaction') }}

)

select
    id                  as raw_transaction_id,
    item_id,
    provider,
    provider_txn_id,
    event_type,
    received_at
from src_raw_transactions
