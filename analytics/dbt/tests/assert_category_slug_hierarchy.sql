-- A child category's slug must be its parent's slug plus one dotted segment.
--
--   education                       (top level, no dot)
--   education.tuition               (child of education)
--
-- This is a convention the application maintains, not something the schema
-- enforces: slug and parent_id are independent columns, so a category can be
-- reparented without its slug changing, or renamed without its parent moving.
--
-- It matters because the slug is the human-readable key — it is what appears in
-- a rule definition, in a URL, and in any hand-written query someone runs
-- against these marts. Once slug and parent_id disagree, `where category_slug
-- like 'food.%'` and `where parent_slug = 'food'` return different sets, and
-- both look like reasonable ways to ask the same question.
--
-- Three distinct violations are reported, because they have different causes:
--   * a child whose slug does not start with its parent's slug and a dot
--   * a child whose slug has more than one segment below its parent (which
--     would mean a third level exists in a two-level taxonomy)
--   * a top-level category whose slug contains a dot, implying a parent it
--     does not have

with categories as (

    select
        category_id,
        category_slug,
        parent_category_id,
        parent_slug,
        is_top_level
    from {{ ref('dim_categories') }}
    where not is_unknown_member

)

select
    category_id,
    category_slug,
    parent_slug,
    case
        when is_top_level and category_slug like '%.%'
            then 'top-level slug contains a dot but has no parent'
        when not is_top_level and category_slug not like parent_slug || '.%'
            then 'child slug is not prefixed by its parent slug'
        else 'child slug is more than one level below its parent'
    end as failure_reason
from categories
where
    (is_top_level and category_slug like '%.%')
    or (
        not is_top_level
        and (
            category_slug not like parent_slug || '.%'
            -- Exactly one dot more than the parent has. A two-level taxonomy
            -- means a child slug has exactly one dot and a parent has none.
            or length(category_slug) - length(replace(category_slug, '.', ''))
               <> length(parent_slug) - length(replace(parent_slug, '.', '')) + 1
        )
    )
