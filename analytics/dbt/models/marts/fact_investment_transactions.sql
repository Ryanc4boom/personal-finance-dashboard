-- One row per investment transaction: trades, distributions, fees, transfers.
--
-- Kept apart from fact_transactions on purpose, mirroring the split in the
-- source schema. They look alike enough that merging them is the obvious
-- simplification, and it silently corrupts every budget in the app: a BUY
-- rearranges value inside an account without changing its total, so folding it
-- into the ledger reports each 401(k) contribution as a $500 expense and each
-- rebalance as thousands of dollars of spending. `is_spendable` on
-- fact_transactions and every budget downstream depend on this staying separate.
--
-- Sign convention is the LEDGER's, not the trade's: amount_cents is positive
-- when cash enters the account (SELL, DIVIDEND, INTEREST, inbound TRANSFER) and
-- negative when it leaves (BUY, FEE, outbound TRANSFER). Same rule as
-- fact_transactions.signed_amount_cents, so the two can be reasoned about
-- together. Note that `quantity` runs the OTHER way — positive on a BUY — which
-- is correct (shares in, cash out) and is exactly the sort of thing that gets
-- "fixed" into a bug.

with investment_transactions as (

    select * from {{ ref('stg_investment_transactions') }}

),

account_ownership as (

    select * from {{ ref('int_account_ownership') }}

)

select
    investment_transactions.investment_transaction_id,

    account_ownership.user_id,

    investment_transactions.account_id,

    -- Left NULL, deliberately, and this is the ONE fact here that does not
    -- coalesce a nullable key onto an unknown member.
    --
    -- On fact_transactions a NULL category means "we failed to resolve one" —
    -- the key was supposed to exist, so a sentinel row named UNKNOWN is an
    -- honest stand-in. Here a NULL means "not applicable": an account fee, a
    -- wire, a cash sweep genuinely has no instrument. Inventing a sentinel
    -- security for those would make `count(distinct security_id)` report one
    -- phantom holding, and would park every fee under a fake ticker in any
    -- per-security P&L. The flag below is the honest version.
    --
    -- The cost of the NULL is that an inner join to dim_securities drops every
    -- cash row, which would understate fees. `has_security` exists so that is a
    -- deliberate filter rather than an accident of join type.
    investment_transactions.security_id,
    investment_transactions.security_id is not null             as has_security,

    investment_transactions.transaction_date,
    (to_char(investment_transactions.transaction_date, 'YYYYMMDD'))::int as date_key,

    -- Measures. Fully additive, unlike fact_holdings.
    investment_transactions.amount_cents,
    investment_transactions.quantity,
    investment_transactions.price_cents,
    investment_transactions.fees_cents,

    -- The measure that makes return attribution possible.
    --
    -- Money the user PUT IN is not investment performance, and separating the
    -- two is the whole question anyone asks of a portfolio: did it grow because
    -- I contributed or because it went up? A BUY nets to zero at the account
    -- level (cash out, shares in) so it must not count; only an external
    -- TRANSFER moves the boundary. Deriving this once here is the point —
    -- every consumer that re-derives it is one bad predicate away from
    -- reporting a year of contributions as a year of gains.
    case
        when investment_transactions.is_external_flow
            then investment_transactions.amount_cents
        else 0
    end                                                         as net_contribution_cents,

    -- Degenerate dimensions: attributes of the event itself, with no dimension
    -- table of their own worth building.
    investment_transactions.investment_transaction_type,
    investment_transactions.investment_transaction_subtype,
    investment_transactions.is_external_flow,

    -- Direction, in the same vocabulary as fact_transactions.direction, so a
    -- union of cash movements across both facts does not need a translation
    -- table. Derived from the sign rather than from `type`: the sign is what
    -- the CHECK constraint upstream actually guarantees, and an inbound versus
    -- outbound TRANSFER shares a single type value.
    case
        when investment_transactions.amount_cents >= 0 then 'INFLOW'
        else 'OUTFLOW'
    end                                                         as direction,

    investment_transactions.investment_transaction_description,
    investment_transactions.investment_transaction_currency,
    investment_transactions.provider_investment_txn_id,
    investment_transactions.investment_transaction_created_at

from investment_transactions
inner join account_ownership
    on investment_transactions.account_id = account_ownership.account_id
