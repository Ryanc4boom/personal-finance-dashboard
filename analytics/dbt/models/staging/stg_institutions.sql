with src_institutions as (

    select * from {{ source('budgeting', 'institution') }}

)

select
    id                          as institution_id,
    provider_institution_id,
    name                        as institution_name,
    created_at                  as institution_created_at
from src_institutions
