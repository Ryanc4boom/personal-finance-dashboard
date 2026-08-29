-- Resolves the ownership chain user → item → account, once.
--
-- This exists because of a specific rule in CLAUDE.md: every query touching
-- user data must filter on ownership, and there is currently ONE seeded user —
-- which means a query returns the right answer whether or not it filters, and
-- a missing filter is invisible until it is a breach.
--
-- A mart whose facts carry no user_id institutionalises exactly that trap: it
-- would look correct forever on single-user data and silently mix two users'
-- money the moment a second one exists. So user_id is resolved here once, and
-- every downstream fact and dimension joins to it rather than each model
-- re-walking the chain and one of them getting it wrong.
--
-- The chain is account → item → user, and `item` is where user_id actually
-- lives; `account` has no user_id column of its own.

with accounts as (

    select * from {{ ref('stg_accounts') }}

),

items as (

    select * from {{ ref('stg_items') }}

),

institutions as (

    select * from {{ ref('stg_institutions') }}

)

select
    accounts.account_id,
    items.item_id,
    items.user_id,
    items.institution_id,

    accounts.account_name,
    accounts.account_type,
    accounts.account_subtype,
    accounts.current_balance_cents,
    accounts.available_balance_cents,
    accounts.credit_limit_cents,
    accounts.is_liability_account,
    accounts.account_is_active,
    accounts.account_created_at,

    items.item_status,
    items.item_error_code,
    items.item_last_synced_at,

    -- Left join: an item can exist before its institution is resolved, and
    -- dropping those accounts would silently lose their transactions from
    -- every mart downstream. An inner join here is the classic way a fact
    -- table quietly loses rows.
    institutions.institution_name,
    institutions.provider_institution_id

from accounts
inner join items
    on accounts.item_id = items.item_id
left join institutions
    on items.institution_id = institutions.institution_id
