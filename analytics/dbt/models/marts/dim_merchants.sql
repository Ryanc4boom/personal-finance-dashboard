-- One row per CANONICAL merchant, not one row per merchant record.
--
-- This is the whole point of the model. The source table holds both canonical
-- merchants and their aliases, linked by canonical_merchant_id. Keying this
-- dimension on `id` would put "STARBUCKS #4412" and "STARBUCKS STORE 4412" in
-- as two separate merchants, and every merchant rollup would then split one
-- merchant's spend across several rows — a chart that is wrong in a way that
-- looks entirely reasonable.
--
-- `alias_count` is carried so the collapsing is visible and auditable rather
-- than implicit: if a canonical merchant suddenly has 200 aliases, the
-- normalisation has gone wrong and this is where it shows.
--
-- The one-level coalesce in stg_merchants is only valid if no alias points at
-- another alias. tests/assert_merchant_alias_depth_one.sql asserts that rather
-- than trusting it — nothing in the schema enforces it.

{% set unknown_key = '00000000-0000-0000-0000-000000000000' %}

with merchants as (

    select * from {{ ref('stg_merchants') }}

),

alias_counts as (

    select
        canonical_merchant_id,
        count(*) - 1    as alias_count   -- excludes the canonical row itself
    from merchants
    group by 1

),

canonical as (

    select
        merchants.merchant_id,
        merchants.merchant_normalized_key,
        merchants.merchant_display_name,
        merchants.merchant_default_category_id,
        merchants.merchant_link_method,
        merchants.merchant_link_score,
        merchants.merchant_is_verified,
        alias_counts.alias_count,
        false   as is_unknown_member
    from merchants
    inner join alias_counts
        on merchants.merchant_id = alias_counts.canonical_merchant_id
    -- Canonical rows only. Aliases are represented by their alias_count, not
    -- by their own row.
    where merchants.is_canonical_merchant

),

unknown_member as (

    select
        '{{ unknown_key }}'::uuid   as merchant_id,
        'unknown'                   as merchant_normalized_key,
        'Unknown Merchant'          as merchant_display_name,
        cast(null as uuid)          as merchant_default_category_id,
        cast(null as varchar)       as merchant_link_method,
        cast(null as integer)       as merchant_link_score,
        false                       as merchant_is_verified,
        0                           as alias_count,
        true                        as is_unknown_member

)

select * from canonical
union all
select * from unknown_member
