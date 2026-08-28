-- One row per category. Two-level taxonomy: `parent_id` is NULL on a top-level
-- category, and `user_id` is NULL on a system category. Both nullable FKs are
-- carried through as-is; flattening the hierarchy happens in dim_categories,
-- and the NULLs are why the tests downstream use relationships_where.

with src_categories as (

    select * from {{ source('budgeting', 'category') }}

)

select
    id                  as category_id,
    user_id,
    parent_id           as parent_category_id,
    slug                as category_slug,
    name                as category_name,
    kind                as category_kind,
    is_system           as category_is_system,
    sort_order          as category_sort_order
from src_categories
