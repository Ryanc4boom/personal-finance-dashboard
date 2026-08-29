"""The code location Dagster loads: `dagster dev -m dagster_budgeting.definitions`."""

from dagster import (
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    EnvVar,
    ScheduleDefinition,
    define_asset_job,
    in_process_executor,
)
from dagster_dbt import DbtCliResource

from .assets_dbt import dbt_analytics_assets
from .assets_ingest import plaid_sync
from .assets_research import research_snapshot
from .project import DBT_TARGET, dbt_project
from .resources import OltpDatabase

# Everything, in dependency order. A single job rather than one per layer:
# the layers are not independently useful — a rebuilt staging table nobody
# promoted to a mart is not a deliverable — and splitting them would let a
# staging refresh succeed while the marts silently stayed stale.
analytics_refresh_job = define_asset_job(
    name="analytics_refresh",
    selection=AssetSelection.all(),
    description="Rebuild and test the warehouse end to end. Safe to re-run.",
    # in_process, not the default multiprocess.
    #
    # `research_snapshot` calls into app.services.sec_client, whose rate limiter
    # is a module-level object throttling to SEC_RATE_LIMIT_PER_SECOND — PER
    # PROCESS. Under the multiprocess executor each step gets its own
    # interpreter and its own limiter, so two steps touching EDGAR would each
    # believe they owned the whole budget and the real request rate would be
    # double what the setting says. SEC enforces by IP ban, and an IP ban is not
    # a failure you notice quickly or recover from on your own schedule.
    #
    # The asset also declares pool="sec_edgar" with a limit of 1. That is not
    # redundancy for its own sake: this line is a property of the job and could
    # be dropped by someone adding parallelism for the dbt half, while the pool
    # is a property of the asset and travels with it.
    #
    # Costs nothing here. There are two root assets and one dbt multi-asset, the
    # run coordinator already allows one run at a time, and dbt does its own
    # internal threading inside a subprocess regardless of this setting.
    executor_def=in_process_executor,
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
    assets=[plaid_sync, research_snapshot, dbt_analytics_assets],
    jobs=[analytics_refresh_job],
    schedules=[daily_refresh_schedule],
    resources={
        # Target is pinned from the same env var dbt reads, so the UI and the
        # CLI cannot end up pointed at different databases. `demo` unless
        # DBT_TARGET says otherwise; see profiles.yml for why `real` is the
        # one that has to be asked for explicitly.
        "dbt": DbtCliResource(project_dir=dbt_project, target=DBT_TARGET),
        # EnvVar, not os.environ. EnvVar resolves when a run starts; os.environ
        # would resolve while the code location loads, so an unset
        # DATABASE_URL would blank the entire UI — every dbt asset included —
        # instead of failing the one asset that needs it, with a message that
        # says which variable is missing.
        "oltp": OltpDatabase(url=EnvVar("DATABASE_URL")),
    },
)
