-- One row per person.
--
-- Exists so that every fact's user_id has a real relationships target — a
-- foreign key that points at nothing is not a tested foreign key.
--
-- The email address is deliberately NOT carried into the mart. It is the one
-- piece of directly identifying information in this schema, and marts are the
-- layer that gets screenshared, rendered by `dbt docs` (which publishes column
-- statistics), and previewed in the Dagster asset UI. `user_label` is derived
-- from the id so the dimension is still readable in a demo without putting a
-- real address on screen.

with users as (

    select * from {{ ref('stg_users') }}

)

select
    user_id,
    'user_' || left(user_id::text, 8)   as user_label,
    -- Domain only. Enough to distinguish personal from work accounts if that
    -- ever matters analytically; not enough to identify anyone.
    split_part(email, '@', 2)           as email_domain,
    user_timezone,
    user_currency,
    user_created_at
from users
