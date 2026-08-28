-- One row per account. `mask` (the last four digits of the real account number)
-- is dropped here rather than in the mart: it identifies a real account, it is
-- rendered by `dbt docs` in the column catalog, and no analytical question
-- needs it.

with src_accounts as (

    select * from {{ source('budgeting', 'account') }}

)

select
    id                          as account_id,
    item_id,
    name                        as account_name,
    type                        as account_type,
    subtype                     as account_subtype,
    current_balance_cents,
    available_balance_cents,
    credit_limit_cents,
    -- Liabilities count against net worth. Derived from the type rather than
    -- from the sign of the balance, because Plaid reports a card balance as a
    -- positive amount owed while the seeds store it negative — the raw sign is
    -- not a reliable input. Mirrors LIABILITY_ACCOUNT_TYPES in app/models/enums.py.
    type in ('credit', 'loan')  as is_liability_account,
    is_active                   as account_is_active,
    created_at                  as account_created_at,
    updated_at                  as account_updated_at
from src_accounts
