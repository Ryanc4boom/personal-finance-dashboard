# Analytics layer: dbt + Dagster.
#
# Python 3.12, not the host's 3.14. dbt-core installs on 3.14 and then dies
# inside mashumaro on the first command, so a host venv is not an option here.
#
# Build context is the REPO ROOT, not this directory, because the image needs
# backend/ as well: the Dagster assets import app.services.ingestion and
# app.services.research rather than reimplementing sync or the XBRL parser.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # dbt looks for profiles.yml here; it lives with the project so one path
    # works for both `docker compose run` and the Dagster code location.
    DBT_PROFILES_DIR=/opt/analytics/dbt \
    # Unset, Dagster writes instance state to a temp dir and silently loses
    # every run, schedule tick and materialization between container restarts.
    DAGSTER_HOME=/opt/dagster_home \
    # /opt/backend lets `import app.services.ingestion` resolve against the
    # mounted backend. /opt/analytics is what makes
    # `dagster dev -m dagster_budgeting.definitions` importable: the Dagster CLI
    # does not add the working directory to sys.path, so relying on working_dir
    # alone fails with ModuleNotFoundError and no hint that the module is
    # sitting right there.
    PYTHONPATH=/opt/analytics:/opt/backend

# libpq-dev + build-essential are for psycopg2 (dbt-postgres); postgresql-client
# gives psql/createdb for the demo-database bootstrap.
# git is not optional: `dbt deps` shells out to it, and `dbt debug` reports a
# failed check without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        postgresql-client \
        git \
    && rm -rf /var/lib/apt/lists/*

# Backend deps first: they change far less often than the analytics code, so
# this layer stays cached across most rebuilds.
COPY backend/requirements.txt /tmp/backend-requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt

COPY analytics/requirements.txt /tmp/analytics-requirements.txt
RUN pip install --no-cache-dir -r /tmp/analytics-requirements.txt

RUN mkdir -p "$DAGSTER_HOME"

WORKDIR /opt/analytics

# Source is bind-mounted by docker-compose so edits are live; nothing is COPYed
# in. That also keeps real credentials out of every image layer.
CMD ["dbt", "--version"]
