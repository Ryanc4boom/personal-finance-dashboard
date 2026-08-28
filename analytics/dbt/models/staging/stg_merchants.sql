-- One row per merchant record, alias or canonical.
--
-- `canonical_merchant_id` is NULL on a canonical row and points at one on an
-- alias. The resolved key is computed here once —
-- coalesce(canonical_merchant_id, id) — because keying anything on `id` would
-- double-count every alias in a merchant rollup: "STARBUCKS #4412" and
-- "STARBUCKS STORE 4412" would appear as two merchants.
--
-- This coalesce is only correct if alias chains are exactly one level deep.
-- That is not enforced by any DB constraint, so tests/assert_merchant_alias_depth_one.sql
-- asserts it rather than assuming it.

with src_merchants as (

    select * from {{ source('budgeting', 'merchant') }}

)

select
    id                                          as merchant_id,
    coalesce(canonical_merchant_id, id)         as canonical_merchant_id,
    canonical_merchant_id                       as alias_of_merchant_id,
    canonical_merchant_id is null               as is_canonical_merchant,
    normalized_key                              as merchant_normalized_key,
    display_name                                as merchant_display_name,
    default_category_id                         as merchant_default_category_id,
    link_method                                 as merchant_link_method,
    link_score                                  as merchant_link_score,
    is_verified                                 as merchant_is_verified
from src_merchants
