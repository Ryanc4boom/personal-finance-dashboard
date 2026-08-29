"""The code location Dagster loads: `dagster dev -m dagster_budgeting.definitions`."""

from dagster import (
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
)
from dagster_dbt import DbtCliResource

from .assets_dbt import dbt_analytics_assets
from .project import DBT_TARGET, dbt_project

# Everything, in dependency order. A single job rather than one per layer:
# the layers are not independently useful — a rebuilt staging table nobody
# promoted to a mart is not a deliverable — and splitting them would let a
# staging refresh succeed while the marts silently stayed stale.
analytics_refresh_job = define_asset_job(
    name="analytics_refresh",
    selection=AssetSelection.all(),
    description="Rebuild and test the warehouse end to end. Safe to re-run.",
)

# Daily, and STOPPED by default.
#
# A schedule that starts itself on first load is a bad default in a repo wired
# to real bank accounts: cloning it and running `dagster dev` should not begin
# hitting Plaid (billed per call) on a timer nobody chose. Turning it on is one
# click in the UI and a deliberate act.
daily_refresh_schedule = ScheduleDefinition(
    name="daily_analytics_refresh",
    job=analytics_refresh_job,
    # 06:15 local. After the overnight posting window most institutions use,
    # and off the hour — every scheduler in the world fires at :00, and Plaid's
    # rate limits are not exempt from that.
    cron_schedule="15 6 * * *",
    default_status=DefaultScheduleStatus.STOPPED,
)

defs = Definitions(
    assets=[dbt_analytics_assets],
    jobs=[analytics_refresh_job],
    schedules=[daily_refresh_schedule],
    resources={
        # Target is pinned from the same env var dbt reads, so the UI and the
        # CLI cannot end up pointed at different databases. `demo` unless
        # DBT_TARGET says otherwise; see profiles.yml for why `real` is the
        # one that has to be asked for explicitly.
        "dbt": DbtCliResource(project_dir=dbt_project, target=DBT_TARGET),
    },
)
