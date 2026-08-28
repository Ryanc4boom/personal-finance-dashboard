#!/usr/bin/env bash
#
# Create the demo database and the restricted role the analytics layer connects
# as. Fully idempotent, and MEANT to be run twice:
#
#   1. before `alembic upgrade head`  — creates the database, the role, and the
#      DEFAULT PRIVILEGES that cover every table Alembic is about to create
#   2. after the seeds have run       — grants SELECT on the tables that now
#      exist, and revokes the access-token column
#
# Running it only once in either position leaves a half-privileged role, so the
# Makefile calls it in both places.
#
# Why a separate role at all: it turns "the analytics layer must never write to
# public" from a convention that a wrong dbt model could violate into something
# the database refuses. budgeting_analytics has SELECT on public and CREATE on
# the database — it cannot UPDATE, DELETE or DROP anything the OLTP app owns.

set -euo pipefail

PGHOST="${PGHOST:-db}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-budget}"
DEMO_DB="${DEMO_DB:-budgeting_demo}"
ANALYTICS_ROLE="${ANALYTICS_PGUSER:-budgeting_analytics}"
ANALYTICS_PASSWORD="${ANALYTICS_PGPASSWORD:-analytics}"

export PGHOST PGPORT PGUSER
export PGPASSWORD="${PGPASSWORD:-budget}"

psql_admin() { psql --no-psqlrc -v ON_ERROR_STOP=1 -d postgres "$@"; }
psql_demo()  { psql --no-psqlrc -v ON_ERROR_STOP=1 -d "$DEMO_DB" "$@"; }

echo "==> waiting for postgres at ${PGHOST}:${PGPORT}"
for _ in $(seq 1 30); do
    pg_isready -q && break
    sleep 1
done
pg_isready || { echo "postgres never became ready" >&2; exit 1; }

# --- database ------------------------------------------------------------- #
# CREATE DATABASE cannot run inside a transaction or take IF NOT EXISTS, hence
# the existence check rather than a DO block.
if [ -z "$(psql_admin -tAc "select 1 from pg_database where datname = '${DEMO_DB}'")" ]; then
    echo "==> creating database ${DEMO_DB}"
    psql_admin -c "CREATE DATABASE \"${DEMO_DB}\""
else
    echo "==> database ${DEMO_DB} already exists"
fi

# --- role ----------------------------------------------------------------- #
echo "==> ensuring role ${ANALYTICS_ROLE}"
psql_admin <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${ANALYTICS_ROLE}') THEN
        CREATE ROLE "${ANALYTICS_ROLE}" LOGIN PASSWORD '${ANALYTICS_PASSWORD}';
    ELSE
        ALTER ROLE "${ANALYTICS_ROLE}" LOGIN PASSWORD '${ANALYTICS_PASSWORD}';
    END IF;
END
\$\$;
SQL

# --- privileges ----------------------------------------------------------- #
echo "==> granting on ${DEMO_DB}"
psql_demo <<SQL
GRANT CONNECT ON DATABASE "${DEMO_DB}" TO "${ANALYTICS_ROLE}";

-- CREATE on the database is what lets dbt run CREATE SCHEMA for
-- analytics_staging / analytics_marts / analytics_test_failures. dbt creates
-- schemas but never databases, which is why this script exists.
GRANT CREATE ON DATABASE "${DEMO_DB}" TO "${ANALYTICS_ROLE}";

-- Read-only on the OLTP tables. Note there is no GRANT INSERT/UPDATE/DELETE
-- anywhere here, and that omission is the point.
GRANT USAGE ON SCHEMA public TO "${ANALYTICS_ROLE}";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "${ANALYTICS_ROLE}";

-- Covers tables a future Alembic migration adds. Without this, the next
-- migration leaves dbt with a permission error on a source it did not
-- previously need, and the cause is very hard to see. Scoped FOR ROLE to the
-- role that actually runs migrations.
ALTER DEFAULT PRIVILEGES FOR ROLE "${PGUSER}" IN SCHEMA public
    GRANT SELECT ON TABLES TO "${ANALYTICS_ROLE}";
SQL

# --- the access token ----------------------------------------------------- #
# Fernet-encrypted at rest, so this is defence in depth rather than the only
# control — but the analytics layer has no reason to read even the ciphertext,
# and denying the column means a `select *` in a staging model fails loudly
# instead of quietly copying it into another schema.
#
# A column-level REVOKE alone does NOT work here, and this was verified rather
# than assumed: `GRANT SELECT ON ALL TABLES` above is a *table-level* grant,
# which implicitly covers every column, and Postgres will not let a column-level
# REVOKE subtract from it. The first version of this script did exactly that and
# the role could still read access_token. The only thing that works is to drop
# the table-level grant on `item` entirely and re-grant column by column.
#
# The column list is read from the catalog rather than hardcoded, so a later
# Alembic migration that adds a column to `item` does not leave the analytics
# role unable to see it — while access_token stays excluded by name.
# Guarded on table existence because this script runs before migrations too.
echo "==> restricting public.item columns for ${ANALYTICS_ROLE}"
psql_demo <<SQL
DO \$\$
DECLARE
    cols text;
BEGIN
    IF to_regclass('public.item') IS NULL THEN
        RETURN;
    END IF;

    EXECUTE format('REVOKE SELECT ON public.item FROM %I', '${ANALYTICS_ROLE}');

    SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)
      INTO cols
      FROM pg_attribute
     WHERE attrelid = 'public.item'::regclass
       AND attnum > 0
       AND NOT attisdropped
       AND attname <> 'access_token';

    EXECUTE format('GRANT SELECT (%s) ON public.item TO %I', cols, '${ANALYTICS_ROLE}');
END
\$\$;
SQL

echo "==> bootstrap complete: ${DEMO_DB} / ${ANALYTICS_ROLE}"
