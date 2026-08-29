-- One row per security.
--
-- `ticker_symbol` is the CONFORMED DIMENSION KEY of this warehouse. It is the
-- only attribute shared by the two otherwise-unrelated source systems here:
-- Plaid's investment feed (what the user actually holds) and the SEC's EDGAR
-- filings (what the underlying company reported). They share no identifier, no
-- name spelling and no update cadence. Conforming on the ticker is what makes
-- "show me the fundamentals of the companies I am actually exposed to" a join
-- rather than a manual lookup.
--
-- security_type and asset_class are kept as SEPARATE axes, matching
-- app/models/enums.py. Collapsing them would be the obvious simplification and
-- it is wrong: an ETF may hold US equity, international equity, bonds or
-- REITs, so deriving exposure from instrument type files every fund into one
-- bucket and reports a diversified portfolio as "100% ETF" — a chart that
-- answers the wrong question convincingly.

with securities as (

    select * from {{ ref('stg_securities') }}

)

select
    security_id,
    provider_security_id,
    ticker_symbol,
    security_name,

    -- The instrument.
    security_type,
    -- What it is exposed to. Deliberately independent of security_type.
    asset_class,

    close_price_cents,
    close_price_as_of,
    security_currency,
    is_cash_equivalent,

    -- Whether this security can be joined to the SEC research marts at all.
    -- An ETF or a money-market sweep has no single filer behind it, so a NULL
    -- here is normal rather than a data problem — surfacing it as a flag stops
    -- someone reading an empty fundamentals join as missing data.
    ticker_symbol is not null
        and security_type in ('EQUITY')     as is_researchable
from securities
