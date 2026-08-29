-- Guards against the VACUOUS GREEN.
--
-- This is the most important structural test here, and the least obvious. Every
-- other test in this project — not_null, unique, relationships,
-- accepted_values, every custom rule — is expressed as "return the rows that
-- violate me, and fail if there are any". On an EMPTY table, all of them
-- return zero rows and all of them pass.
--
-- So a dbt build against a database that was never seeded, or where the
-- migration ran but the ingestion silently failed, produces empty marts and a
-- completely green test suite. That is the exact opposite of the requirement:
-- the pipeline reports healthy at the precise moment it has no data.
--
-- The same trap appears elsewhere in this repo: a scanner reporting clean may
-- not be scanning. Zero findings and zero coverage look identical from the
-- outside, and the only defence is a test that asserts presence rather than
-- absence.
--
-- Deliberately NOT included:
--   * bridge_transaction_tags — legitimately empty when nothing is tagged, and
--     the demo seeds tag nothing. Asserting rows here would force fake data.
--   * dim_* — a dimension can be legitimately small, and each already has
--     not_null/unique coverage that is only vacuous if the fact is also empty,
--     which is what this catches.

{% set required_facts = [
    'fact_transactions',
    'fact_holdings',
    'fact_investment_transactions',
] %}

with row_counts as (

{% for fact in required_facts %}
    select '{{ fact }}' as model_name, count(*) as row_count from {{ ref(fact) }}
    {% if not loop.last %}union all{% endif %}
{% endfor %}

)

select
    model_name,
    row_count,
    'fact table is empty — every other test passes vacuously' as failure_reason
from row_counts
where row_count = 0
