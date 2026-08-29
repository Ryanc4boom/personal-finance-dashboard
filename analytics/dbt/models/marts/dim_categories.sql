-- One row per category, with the two-level hierarchy flattened.
--
-- Includes an UNKNOWN MEMBER row keyed on the all-zeroes UUID. Facts coalesce
-- their nullable category_id onto it, which buys three things:
--
--   1. fact_transactions has no NULL foreign keys, so a `relationships` test
--      on it is meaningful rather than vacuously skipping the NULL rows.
--   2. An inner join from a fact to this dimension cannot silently drop
--      uncategorised spend. That is the classic way a spend chart quietly
--      under-reports: the total is wrong and nothing errors.
--   3. "Uncategorised" becomes a visible bar on a chart instead of a gap,
--      which is what actually prompts someone to go and categorise it.
--
-- `rollup_slug` is the top-level ancestor — its own slug for a parent, its
-- parent's slug for a child. Precomputing it means a category-level chart is a
-- plain GROUP BY rather than a conditional in every consumer.

{% set unknown_key = '00000000-0000-0000-0000-000000000000' %}

with categories as (

    select * from {{ ref('stg_categories') }}

),

parents as (

    select
        category_id     as parent_category_id,
        category_slug   as parent_slug,
        category_name   as parent_name
    from categories

),

flattened as (

    select
        categories.category_id,
        categories.user_id,
        categories.category_slug,
        categories.category_name,
        categories.category_kind,
        categories.category_is_system,
        categories.category_sort_order,
        categories.parent_category_id,
        parents.parent_slug,
        parents.parent_name,

        categories.parent_category_id is null                       as is_top_level,
        coalesce(parents.parent_slug, categories.category_slug)     as rollup_slug,
        coalesce(parents.parent_name, categories.category_name)     as rollup_name,
        false                                                       as is_unknown_member

    from categories
    left join parents
        on categories.parent_category_id = parents.parent_category_id

),

unknown_member as (

    select
        '{{ unknown_key }}'::uuid   as category_id,
        cast(null as uuid)          as user_id,
        'unknown'                   as category_slug,
        'Uncategorized'             as category_name,
        -- 'UNKNOWN' is not a CategoryKind in app/models/enums.py, and that is
        -- deliberate: the sentinel must not masquerade as a real EXPENSE and
        -- get swept into a budget total. The mart's accepted_values list
        -- therefore differs from the source enum by exactly this value.
        'UNKNOWN'                   as category_kind,
        true                        as category_is_system,
        -1                          as category_sort_order,
        cast(null as uuid)          as parent_category_id,
        cast(null as varchar)       as parent_slug,
        cast(null as varchar)       as parent_name,
        true                        as is_top_level,
        'unknown'                   as rollup_slug,
        'Uncategorized'             as rollup_name,
        true                        as is_unknown_member

)

select * from flattened
union all
select * from unknown_member
