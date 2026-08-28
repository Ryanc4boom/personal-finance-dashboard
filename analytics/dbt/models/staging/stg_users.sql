-- One row per person.
--
-- The CTE is named `src_users`, not `users` and emphatically not `user`:
-- `user` is a reserved word in PostgreSQL and dbt does not quote CTE names.
-- source() quotes the relation, which is the only reason the FROM clause works.

with src_users as (

    select * from {{ source('budgeting', 'user') }}

)

select
    id              as user_id,
    email,
    timezone        as user_timezone,
    currency        as user_currency,
    created_at      as user_created_at,
    updated_at      as user_updated_at
from src_users
