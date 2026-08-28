-- One row per investment transaction (buy, sell, dividend, fee, transfer).
--
-- `is_external_flow` mirrors EXTERNAL_FLOW_TYPES in app/models/enums.py and is
-- the distinction that makes return attribution possible at all: a BUY
-- converts cash into shares and leaves the account's total value unchanged,
-- while a TRANSFER changes it. Without this flag, every mart that tries to
-- separate contributions from market return has to re-derive it, and one of
-- them will get it wrong.

with src_investment_transactions as (

    select * from {{ source('budgeting', 'investment_transaction') }}

)

select
    id                              as investment_transaction_id,
    account_id,
    security_id,
    provider_investment_txn_id,
    type                            as investment_transaction_type,
    subtype                         as investment_transaction_subtype,
    date                            as transaction_date,
    amount_cents,
    quantity,
    price_cents,
    fees_cents,
    type in ('TRANSFER')            as is_external_flow,
    description                     as investment_transaction_description,
    currency                        as investment_transaction_currency,
    created_at                      as investment_transaction_created_at
from src_investment_transactions
