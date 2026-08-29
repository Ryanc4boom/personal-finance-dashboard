-- One row per account, flattened with its item and institution.
--
-- `mask` (the last four digits of the real account number) was dropped back in
-- staging and does not reappear here. A dimension is the layer people put on a
-- screen.

with account_ownership as (

    select * from {{ ref('int_account_ownership') }}

)

select
    account_id,
    user_id,
    item_id,
    institution_id,

    account_name,
    account_type,
    account_subtype,
    coalesce(institution_name, 'Unknown Institution')   as institution_name,

    current_balance_cents,
    available_balance_cents,
    credit_limit_cents,

    is_liability_account,
    -- Sign for net-worth arithmetic, derived from the account TYPE rather than
    -- from the stored balance's sign. services/net_worth.signed_balance_cents
    -- does the same, and for the same reason: Plaid reports a card balance as a
    -- positive amount owed while the seeds store it negative, so the raw sign
    -- is not a reliable input. Trusting it would make a credit card either add
    -- to or subtract from net worth depending on where the row came from.
    case when is_liability_account then -1 else 1 end    as net_worth_sign,

    account_is_active,
    item_status,
    item_error_code,
    item_last_synced_at,
    account_created_at
from account_ownership
