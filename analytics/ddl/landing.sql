-- Landing tables for the SEC/XBRL research snapshots.
--
-- WHY THIS FILE EXISTS AT ALL
--
-- `app/services/research.py` persists nothing. It fetches EDGAR, caches in
-- Redis, and returns a transient ResearchReport; there is no filings, company
-- or facts table among the OLTP tables. So unlike every other source in this
-- project, there is no Postgres data for dbt to transform — the analytics layer
-- has to LAND it first.
--
-- That is not a workaround, it is the interesting part. A request-scoped module
-- can only ever answer "what does this company look like today". Landing a
-- dated snapshot on every run turns the same framework into a time series:
-- which check flipped PASS to FAIL, and when. The live module structurally
-- cannot answer that, because it keeps nothing.
--
-- WHY NOT ALEMBIC
--
-- These tables belong to the analytics layer, not the app. Putting them in
-- `public` under Alembic would make the OLTP app's migration history own a
-- warehouse concern, and `backend/alembic/env.py` leaves include_schemas=False
-- — which is exactly what keeps every analytics_* schema invisible to
-- autogenerate. Landing outside `public` is what lets the two halves evolve
-- without fighting over one revision table.
--
-- WHY NO user_id
--
-- Every other fact and dimension in this warehouse carries user_id with a
-- not_null and a relationships test, because CLAUDE.md rule 2 is that with one
-- seeded user a missing ownership filter is invisible. These three tables are
-- the deliberate exception, and the reason is that they contain no user data:
-- Apple's 2024 revenue is a public filing, identical for every user of this
-- app, and stamping a user_id on it would imply an ownership that does not
-- exist and invite a per-user copy of the same EDGAR row.
--
-- The join back to a person happens downstream, on ticker, against
-- dim_securities — which IS user-scoped. That is the conformed dimension, and
-- keeping the public data un-scoped is what makes it conformable.
--
-- Idempotent: safe to run on every asset materialisation, which is how it is
-- actually invoked.

CREATE SCHEMA IF NOT EXISTS analytics_landing;

-- One row per company ever researched. Keyed on ticker rather than cik because
-- ticker is the conformed key to dim_securities; cik is carried because it is
-- the SEC's actual identity and a ticker can be reassigned between companies.
CREATE TABLE IF NOT EXISTS analytics_landing.research_company (
    ticker              text        PRIMARY KEY,
    cik                 bigint      NOT NULL,
    company_name        text        NOT NULL,
    -- The most recent run. Distinct from the per-snapshot dates below: this is
    -- "when did we last look", which is what tells you a company has gone stale.
    last_snapshot_date  date        NOT NULL,
    first_snapshot_date date        NOT NULL,
    snapshot_count      integer     NOT NULL DEFAULT 1,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- One row per company per fiscal year.
--
-- Grain is (ticker, fiscal_period_end) and NOT (ticker, snapshot_date,
-- fiscal_period_end). A fiscal year's numbers are a fact about the world, not
-- about when we looked, so re-running today must overwrite rather than
-- accumulate a second identical copy of FY2024. Restatements and split
-- renormalisations legitimately change these values, and the upsert lets the
-- newer reading win.
--
-- Money is integer cents and ratios are basis points, matching the OLTP
-- convention exactly. diluted_eps is numeric, not float: it is a quotient, and
-- a float round-trip is silent corruption.
CREATE TABLE IF NOT EXISTS analytics_landing.research_fundamentals (
    ticker                     text    NOT NULL,
    fiscal_period_end          date    NOT NULL,
    fiscal_label               text    NOT NULL,
    -- When this reading was taken. Not part of the key, but kept so a restated
    -- figure can be dated.
    snapshot_date              date    NOT NULL,
    cik                        bigint  NOT NULL,

    revenue_cents              bigint,
    gross_profit_cents         bigint,
    gross_margin_bps           integer,
    operating_income_cents     bigint,
    operating_margin_bps       integer,
    net_income_cents           bigint,
    net_margin_bps             integer,
    diluted_shares             bigint,
    diluted_eps                numeric(18, 6),
    operating_cash_flow_cents  bigint,
    capex_cents                bigint,
    free_cash_flow_cents       bigint,
    cash_and_sti_cents         bigint,
    total_debt_cents           bigint,
    long_term_debt_cents       bigint,
    equity_cents               bigint,
    total_liabilities_cents    bigint,
    interest_expense_cents     bigint,

    updated_at                 timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (ticker, fiscal_period_end)
);

-- One row per company per snapshot per check.
--
-- Here snapshot_date IS part of the key, and that asymmetry with the table
-- above is the whole point of landing this data. A check is a verdict rendered
-- at a moment against the framework as it stood; keeping one row per day is
-- what makes "this company's leverage check flipped to FAIL in March"
-- answerable. Overwriting would throw away the only thing the live module
-- cannot give you.
--
-- stage_status is denormalised onto every check row on purpose. Stage.status in
-- research.py is a derived property — worst verdict wins, and an UNKNOWN cannot
-- be papered over by a PASS. Landing both the parts and the whole is what lets
-- a dbt test re-derive one from the other and catch a transformation that
-- quietly disagrees with the service.
CREATE TABLE IF NOT EXISTS analytics_landing.research_check (
    ticker           text        NOT NULL,
    snapshot_date    date        NOT NULL,
    stage_key        text        NOT NULL,
    check_key        text        NOT NULL,

    stage_number     integer     NOT NULL,
    stage_title      text        NOT NULL,
    stage_status     text        NOT NULL,

    check_label      text        NOT NULL,
    status           text        NOT NULL,
    value_bps        integer,
    value_cents      bigint,

    updated_at       timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (ticker, snapshot_date, stage_key, check_key)
);

-- Deliberately NOT landed: value_display, target_display and detail.
--
-- Those are rendered prose built for a reader ("Operating cash flow $1.2B less
-- capital expenditure $300M"), and they are the one part of the report that
-- interpolates money into a string. A warehouse column holding a formatted
-- currency amount is a column that gets logged, exported to CSV and pasted into
-- a ticket. The machine-readable value_bps / value_cents carry everything a
-- mart can actually aggregate, and the display strings stay in the API response
-- where they are consumed and discarded.

-- Read access for the restricted dbt role, if it exists yet. Guarded because
-- this file runs from the ingestion asset as the owning role, which may execute
-- before the bootstrap script has created budgeting_analytics.
DO $$
DECLARE
    analytics_role text := 'budgeting_analytics';
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = analytics_role) THEN
        RETURN;
    END IF;

    EXECUTE format('GRANT USAGE ON SCHEMA analytics_landing TO %I', analytics_role);
    EXECUTE format(
        'GRANT SELECT ON ALL TABLES IN SCHEMA analytics_landing TO %I', analytics_role
    );
    -- SELECT only. The dbt role reads this schema and writes its own; it has no
    -- more business mutating a landed filing than it does mutating public.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA analytics_landing '
        'GRANT SELECT ON TABLES TO %I', analytics_role
    );
END
$$;
