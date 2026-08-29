"""The dbt project as Dagster assets.

One `@dbt_assets` function backs every model in `analytics/dbt`. Each model is
a first-class asset and each dbt test is an asset check, which is the whole
reason this project uses Dagster rather than Airflow: an Airflow DAG would show
a single opaque `dbt build` task, and the lineage would live only in dbt docs.
"""

from collections.abc import Mapping
from typing import Any

from dagster import AssetExecutionContext, AssetKey
from dagster_dbt import (
    DagsterDbtTranslator,
    DagsterDbtTranslatorSettings,
    DbtCliResource,
    dbt_assets,
)

from .project import dbt_project


class BudgetingDbtTranslator(DagsterDbtTranslator):
    """Names the assets. Only source keys are customised; models keep theirs."""

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> AssetKey:
        # dbt's default key for a source is the bare table name, which would put
        # `transaction` and `account` — OLTP tables this layer only reads — in
        # the same flat namespace as the marts it builds. Prefixing them makes
        # the graph legible at a glance: everything under `oltp/` is somebody
        # else's data that we do not own or write.
        if dbt_resource_props["resource_type"] == "source":
            return AssetKey(["oltp", dbt_resource_props["name"]])

        return super().get_asset_key(dbt_resource_props)

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        # Group by layer, so the UI groups staging / intermediate / marts the
        # same way the folders do. dbt's own default would group by the model's
        # `group` config, which this project does not use.
        fqn = dbt_resource_props.get("fqn") or []
        for layer in ("staging", "intermediate", "marts"):
            if layer in fqn:
                return layer
        return None


dagster_dbt_translator = BudgetingDbtTranslator(
    settings=DagsterDbtTranslatorSettings(
        # Turns every dbt test into a Dagster asset check attached to the model
        # it tests, instead of burying pass/fail in the run log.
        #
        # Caveat worth knowing rather than discovering: a test with more than
        # one parent model cannot become an asset check, because a check belongs
        # to exactly one asset. Four of the singular tests here are like that —
        # assert_facts_not_empty spans three facts, assert_unknown_members_exist
        # spans two dimensions. They still RUN, and they still fail the build;
        # they just do not show up as a check next to a model in the UI.
        enable_asset_checks=True,
    )
)


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=dagster_dbt_translator,
)
def dbt_analytics_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    """Builds and tests the warehouse.

    `dbt build`, NOT `dbt run` followed by `dbt test`. This is the single most
    important decision in the orchestration.

    `build` interleaves per node: it materialises a model, immediately runs that
    model's tests, and SKIPS every dependent if a test fails. `run` then `test`
    would materialise the entire mart layer on top of known-bad data and only
    complain afterwards — the marts would be wrong, published, and green until
    someone read the second step's output. The requirement is that bad data does
    not flow downstream, and only `build` actually enforces that; a separate
    test step reports on damage already done.

    It also means the skipping is done by dbt, inside one process, using the
    real model DAG. Dagster's own blocking-check machinery cannot do this here
    because every model in this project lives in a single multi-asset op, and
    checks gate across ops rather than within one. Relying on Dagster for it
    would be a guard that quietly does nothing.

    A failing test therefore produces: a non-zero dbt exit, a raised exception,
    a red run, red asset checks on the offending model, and dependents reported
    as never materialised. Nothing here can go silently green.
    """
    yield from (
        dbt.cli(["build"], context=context)
        .stream()
        # Row counts per model, attached as materialisation metadata. Counts are
        # the one class of number this repo's rules allow to be recorded freely,
        # and they make "did the pipeline actually do anything" answerable from
        # the UI without opening psql.
        #
        # Deliberately NOT .fetch_column_metadata(): that profiles each column
        # and stores min/max/null counts in the Dagster event log. On this
        # schema the min and max of `signed_amount_cents` are the user's largest
        # transactions, and the event log is a SQLite file with none of the
        # protections the OLTP database has. Row counts are a count; column
        # stats are the data.
        .fetch_row_counts()
    )
