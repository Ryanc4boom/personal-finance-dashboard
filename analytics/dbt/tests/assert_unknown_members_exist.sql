-- Every dimension that facts coalesce a nullable key onto must actually
-- contain its unknown member.
--
-- fact_transactions does
--     coalesce(category_id, '00000000-...-000000000000'::uuid)
-- so that it has no NULL foreign keys and an inner join cannot silently drop
-- uncategorised spend. That coalesce points at a row which has to be there.
--
-- Without this test the failure is delayed and misattributed: the relationships
-- test on fact_transactions.category_id only fails once an UNCATEGORISED
-- transaction actually appears. On a fully-categorised demo database the
-- sentinel is never referenced, so a missing unknown member is invisible until
-- real data arrives — at which point the error points at the fact table rather
-- than at the dimension that lost its sentinel row.
--
-- Asserting a row EXISTS is the inverse shape of every generic dbt test, which
-- is why this cannot be an expression_is_true: that macro returns the rows
-- failing an expression, so "at least one row matches" has no natural
-- expression form. A singular test is the right tool.

{% set unknown_key = '00000000-0000-0000-0000-000000000000' %}

with expected as (

    select 'dim_categories' as model_name, count(*) as member_count
    from {{ ref('dim_categories') }}
    where is_unknown_member and category_id = '{{ unknown_key }}'::uuid

    union all

    select 'dim_merchants' as model_name, count(*) as member_count
    from {{ ref('dim_merchants') }}
    where is_unknown_member and merchant_id = '{{ unknown_key }}'::uuid

)

select
    model_name,
    member_count,
    case
        when member_count = 0
            then 'unknown member row is missing — facts coalesce onto a key that does not exist'
        else 'more than one unknown member row'
    end as failure_reason
from expected
where member_count <> 1
