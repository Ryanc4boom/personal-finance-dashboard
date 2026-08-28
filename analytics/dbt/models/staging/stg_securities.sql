-- One row per security.
--
-- `ticker_symbol` is upper-cased and trimmed here because it is the CONFORMED
-- KEY between two independent source systems: Plaid's investment feed and the
-- SEC's EDGAR filings. Those two systems agree on nothing else — no shared id,
-- no shared name spelling — so if the case or whitespace differs, the join in
-- the research marts silently produces zero rows rather than an error.
-- NULLIF on the empty string keeps '' from becoming a joinable key.

with src_securities as (

    select * from {{ source('budgeting', 'security') }}

)

select
    id                                              as security_id,
    provider_security_id,
    nullif(upper(trim(ticker_symbol)), '')          as ticker_symbol,
    name                                            as security_name,
    type                                            as security_type,
    asset_class,
    close_price_cents,
    close_price_as_of,
    currency                                        as security_currency,
    is_cash_equivalent
from src_securities
