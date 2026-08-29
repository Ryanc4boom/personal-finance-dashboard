# Analytics layer entry points.
#
# The demo targets point at `budgeting_demo`, a SEPARATE database seeded with
# synthetic fixtures — never at the development database wired to real accounts.
# That is not a convenience, it is the only reason any of this is screenshotable:
# the marts, `dbt docs`, and the Dagster asset graph all faithfully render
# whatever they are pointed at, and pointed at the real database every one of
# them is a picture of somebody's finances.
#
# Everything runs in the analytics container. dbt-core does not support Python
# 3.14, which is what the backend venv on this machine runs, so there is no host
# venv to fall back to — see analytics/requirements.txt.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE        := docker compose --profile analytics
DEMO_DB        ?= budgeting_demo

# Credentials come from the environment with the same defaults docker-compose.yml
# uses, never inlined. Writing the URL out literally is what CLAUDE.md rule 1
# forbids, and .githooks/pre-commit refuses the commit — correctly — if you try:
# a connection string in a public repo is a credential regardless of how
# uninteresting the credential currently is. Overriding POSTGRES_PASSWORD in the
# environment now works here exactly as it already did for compose.
POSTGRES_USER     ?= budget
POSTGRES_PASSWORD ?= budget
DEMO_DB_URL    := postgresql+psycopg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@db:5432/$(DEMO_DB)
BACKEND        := docker compose run --rm --no-deps -e DATABASE_URL=$(DEMO_DB_URL)

.PHONY: help demo demo-down demo-reset refresh dbt-build dbt-test dbt-docs dagster \
        analytics-test verify-gitignore seed migrate bootstrap

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- #
# The one command
# --------------------------------------------------------------------------- #

demo: bootstrap migrate seed bootstrap refresh  ## Build the whole demo warehouse from cold
	@echo
	@echo "  Warehouse built against $(DEMO_DB)."
	@echo "  Next: make dagster   (asset graph + lineage at http://localhost:3030)"
	@echo "        make dbt-docs  (dbt's own lineage graph at http://localhost:8085)"
	@echo

# The demo runs the real ORCHESTRATED job, not a bare `dbt build`. That is the
# whole point: ingestion, research and transformation in one dependency-ordered
# run, which is the thing being demonstrated. `make dbt-build` still exists for
# iterating on models alone.
#
# NETWORK REQUIRED, and only for this step. plaid_sync skips the seeded items by
# prefix so no Plaid call is billed, but research_snapshot really does fetch
# EDGAR — and assert_research_conforms_to_portfolio really will fail the build
# if it landed nothing. That coupling is deliberate and lives in exactly one
# test file so it can be excluded by name if a run has to work offline.
refresh:  ## Run the full pipeline: ingest, research, then build and test
	$(COMPOSE) run --rm --entrypoint bash dagster -lc \
		"cd /opt/analytics && dagster job execute -j analytics_refresh -m dagster_budgeting.definitions"

# bootstrap runs TWICE in `demo`, and the repetition is deliberate rather than a
# copy-paste error. The first call creates the database and the DEFAULT
# PRIVILEGES that cover tables Alembic has not created yet; the second grants
# SELECT on the tables that now exist and re-applies the access_token column
# revoke. Running it only once in either position leaves the analytics role
# half-privileged, and the resulting dbt error names a permission rather than
# the missing step.
bootstrap:  ## Create the demo database and the restricted analytics role
	$(COMPOSE) run --rm analytics-bootstrap

migrate:  ## Run Alembic against the demo database
	$(BACKEND) api alembic upgrade head

# pydantic-settings prefers a real environment variable over .env, so pointing
# the seeds at another database needs no code change at all — just the override
# on the line above.
#
# ORDER MATTERS and is not enforced by the seeds themselves. `demo` builds a
# CategoryResolver in its first few lines, which raises outright if the
# taxonomy is not already present, and `investments` needs the accounts `demo`
# creates. Running them out of order fails with a message that names the fix,
# which is good — but a Makefile that reproduces the right order means nobody
# has to read it. Each is individually idempotent, so re-running is a no-op.
seed:  ## Load the deterministic synthetic fixtures (taxonomy, ledger, portfolio)
	$(BACKEND) api python -m app.seeds.categories
	$(BACKEND) api python -m app.seeds.demo
	$(BACKEND) api python -m app.seeds.investments

# --------------------------------------------------------------------------- #
# dbt
# --------------------------------------------------------------------------- #

# `build`, never `run` then `test`. build interleaves per node — it materialises
# a model, immediately runs that model's tests, and SKIPS every dependent if one
# fails. `run` followed by `test` would rebuild the entire mart layer on top of
# known-bad data and only complain afterwards, by which point the marts are
# wrong, published, and green.
dbt-build:  ## Rebuild and test the warehouse (safe to re-run)
	$(COMPOSE) run --rm dbt build

dbt-test:  ## Run the tests only, against whatever is already built
	$(COMPOSE) run --rm dbt test

dbt-docs:  ## Serve dbt's lineage graph on :8085
	$(COMPOSE) run --rm --service-ports --entrypoint sh dbt -c \
		"dbt docs generate && dbt docs serve --port 8085 --no-browser"

# --------------------------------------------------------------------------- #
# Dagster
# --------------------------------------------------------------------------- #

dagster:  ## Serve the Dagster asset graph on :3030
	$(COMPOSE) up dagster

analytics-test:  ## Run the analytics pytest suite
	$(COMPOSE) run --rm --entrypoint bash dagster -lc \
		"cd /opt/analytics && python -m pytest tests -q"

# --------------------------------------------------------------------------- #
# Teardown and hygiene
# --------------------------------------------------------------------------- #

# Stops the analytics services and drops the demo database, but leaves the
# `pgdata` volume — and therefore the development database — alone. `docker
# compose down -v` would take both, which is a very expensive way to tidy up.
demo-down:  ## Stop analytics services and drop the demo database
	-$(COMPOSE) stop dagster
	-$(COMPOSE) rm -f dagster dbt analytics-bootstrap
	-docker compose exec -T db psql -U $(POSTGRES_USER) -d postgres \
		-c "DROP DATABASE IF EXISTS \"$(DEMO_DB)\" WITH (FORCE)"

demo-reset: demo-down demo  ## Drop and rebuild the demo warehouse from scratch

# Asserts BOTH directions, because only checking one is how this rule broke in
# the first place. `.gitignore` carries `*.sql` — a pg_dump of this database
# holds live Plaid tokens and every real transaction, which is a worse leak than
# .env — so every dbt model needed an explicit negation to be committable at
# all. A negation written slightly too broadly would un-ignore the dumps too.
verify-gitignore:  ## Assert dbt models are tracked and SQL dumps still are not
	@fail=0; \
	for f in analytics/dbt/models/marts/fact_transactions.sql \
	         analytics/dbt/models/marts/dim_companies.sql \
	         analytics/dbt/tests/assert_facts_not_empty.sql \
	         analytics/ddl/landing.sql; do \
		if git check-ignore -q "$$f"; then \
			echo "  FAIL  $$f is ignored and would never be committed"; fail=1; \
		else echo "  ok    $$f tracked"; fi; \
	done; \
	for f in dump.sql backend/dump.sql analytics/pg_dump.sql; do \
		if git check-ignore -q "$$f"; then echo "  ok    $$f ignored"; \
		else echo "  FAIL  $$f is NOT ignored — a database dump could be committed"; fail=1; fi; \
	done; \
	exit $$fail
