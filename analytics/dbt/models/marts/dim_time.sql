-- One row per calendar day, spanning every date any fact refers to.
--
-- Built with native generate_series rather than dbt_utils.date_spine. The
-- macro needs its bounds at COMPILE time, as literals or as a var — it cannot
-- take `select min(date) from ...`. That would mean either hardcoding a start
-- date (which silently drops history the moment a backfill lands earlier than
-- the guess) or passing bounds in as vars (which makes the model unbuildable
-- without them, and breaks `dbt build --empty`). generate_series evaluates at
-- RUN time, so the spine simply follows the data.
--
-- The bounds are padded to whole month boundaries so month-level aggregates
-- always have a complete dimension to join to; a partial trailing month is a
-- classic source of a chart whose last bar is mysteriously short.

with fact_dates as (

    select transaction_date as calendar_date from {{ ref('stg_transactions') }}
    union all
    select as_of_date                        from {{ ref('stg_holdings') }}
    union all
    select transaction_date                  from {{ ref('stg_investment_transactions') }}

),

bounds as (

    select
        -- coalesce so an unseeded database yields a small valid spine rather
        -- than zero rows. Zero rows here would make every fact's date join
        -- drop silently, which looks like "no data" instead of "no dimension".
        date_trunc('month', coalesce(min(calendar_date), current_date))::date       as start_date,
        (date_trunc('month', coalesce(max(calendar_date), current_date))
            + interval '1 month - 1 day')::date                                    as end_date
    from fact_dates

),

spine as (

    select generate_series(start_date, end_date, interval '1 day')::date as date_day
    from bounds

)

select
    date_day,

    -- Integer surrogate in YYYYMMDD form. Human-readable in a WHERE clause,
    -- and sorts correctly as an integer, unlike a text key.
    (to_char(date_day, 'YYYYMMDD'))::int             as date_key,

    extract(year    from date_day)::int              as year_number,
    extract(quarter from date_day)::int              as quarter_number,
    extract(month   from date_day)::int              as month_number,
    extract(day     from date_day)::int              as day_of_month,
    extract(isodow  from date_day)::int              as iso_day_of_week,
    extract(week    from date_day)::int              as iso_week_number,
    extract(doy     from date_day)::int              as day_of_year,

    to_char(date_day, 'YYYY-MM')                     as year_month,
    to_char(date_day, 'Mon')                         as month_short_name,
    to_char(date_day, 'Month')                       as month_name,
    to_char(date_day, 'Dy')                          as day_short_name,

    -- Period-start columns, one per BudgetPeriod in app/models/enums.py
    -- (MONTHLY / WEEKLY / QUARTERLY / YEARLY). Precomputing them is what lets
    -- a budget-vs-actual query join on equality instead of re-truncating the
    -- date in every consumer — and a date_trunc in a WHERE clause is also what
    -- stops an index from being used.
    -- ISO weeks start Monday, matching extract(isodow).
    date_trunc('week',    date_day)::date            as week_start_date,
    date_trunc('month',   date_day)::date            as month_start_date,
    date_trunc('quarter', date_day)::date            as quarter_start_date,
    date_trunc('year',    date_day)::date            as year_start_date,

    (date_trunc('month', date_day) + interval '1 month - 1 day')::date as month_end_date,

    extract(isodow from date_day) in (6, 7)          as is_weekend,
    date_day = (date_trunc('month', date_day) + interval '1 month - 1 day')::date
                                                     as is_month_end,
    date_day = date_trunc('month', date_day)::date   as is_month_start,

    date_day <= current_date                         as is_past_or_today
from spine
