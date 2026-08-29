-- Merchant alias chains must be exactly one level deep.
--
-- stg_merchants resolves an alias to its canonical merchant with a single
-- coalesce(canonical_merchant_id, id). That is only correct if a row pointed at
-- by canonical_merchant_id is itself canonical. If an alias can point at
-- another alias — A → B → C — the coalesce stops at B, and dim_merchants ends
-- up with B (an alias) as a dimension row while some transactions resolve to B
-- and others to C. One merchant, silently split in two.
--
-- Nothing in the schema prevents this. canonical_merchant_id is a plain
-- self-referencing FK; it does not know the difference between pointing at a
-- canonical row and pointing at an alias.
--
-- Fixing it properly would need a recursive CTE in stg_merchants. That
-- complexity is not worth carrying for a case the data does not currently
-- contain — but the ASSUMPTION has to be tested, or the day it stops holding is
-- the day the merchant report quietly goes wrong.
--
-- Note: the demo seeds contain no aliases at all, so this test currently
-- passes on zero candidate rows. That is stated plainly rather than hidden —
-- it is a guard for real ingested data, not something the demo exercises.

with merchants as (

    select
        merchant_id,
        alias_of_merchant_id
    from {{ ref('stg_merchants') }}

),

aliases as (

    select
        merchant_id         as alias_id,
        alias_of_merchant_id as points_at
    from merchants
    where alias_of_merchant_id is not null

)

select
    aliases.alias_id,
    aliases.points_at,
    target.alias_of_merchant_id as target_points_at,
    'alias points at another alias — coalesce resolution is one level only'
        as failure_reason
from aliases
inner join merchants as target
    on aliases.points_at = target.merchant_id
where target.alias_of_merchant_id is not null
