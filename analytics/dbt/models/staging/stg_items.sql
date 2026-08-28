-- One linked bank login (a Plaid Item).
--
-- Columns are listed explicitly and `access_token` is absent. That is not
-- merely tidy: the budgeting_analytics role has had SELECT revoked on that
-- column, so a `select *` here would fail with "permission denied for table
-- item". Two independent controls, deliberately.

with src_items as (

    select
        id,
        user_id,
        institution_id,
        provider_item_id,
        status,
        last_synced_at,
        error_code,
        created_at,
        updated_at
    from {{ source('budgeting', 'item') }}

)

select
    id                  as item_id,
    user_id,
    institution_id,
    provider_item_id,
    status              as item_status,
    error_code          as item_error_code,
    last_synced_at      as item_last_synced_at,
    created_at          as item_created_at,
    updated_at          as item_updated_at
from src_items
