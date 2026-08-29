"""Locates the dbt project and makes sure it is parseable before Dagster loads.

Kept out of definitions.py because it runs at import time and can fail, and a
failure here should read as "the dbt project is not ready" rather than as a
broken code location.
"""

import os
import subprocess
from pathlib import Path

from dagster_dbt import DbtProject

# analytics/dagster_budgeting/project.py -> analytics/dbt
DBT_PROJECT_DIR = Path(__file__).joinpath("..", "..", "dbt").resolve()

# Same env var dbt itself reads, same default. Resolved here rather than left
# to dbt so the Dagster UI shows which database it is pointed at: `demo` and
# `real` differ by whether the asset previews contain synthetic money or the
# user's actual bank data, and that is not a difference to leave implicit.
DBT_TARGET = os.getenv("DBT_TARGET", "demo")


def _ensure_packages_installed() -> None:
    """Run `dbt deps` if the package directory is missing.

    Without this, a cold checkout fails at parse time with a compilation error
    naming a macro (`dbt_utils.generate_surrogate_key` is not defined) inside
    whichever model happens to be parsed first. That points at a model that is
    perfectly fine and says nothing about the actual cause, which is that
    `dbt deps` was never run — `dbt_packages/` is gitignored, so it is missing
    on every fresh clone and in every rebuilt container by definition.

    Checked by directory presence rather than by always running deps: `dbt deps`
    hits the network, and doing that on every code-location load would make the
    Dagster UI unopenable offline.
    """
    if (DBT_PROJECT_DIR / "dbt_packages").is_dir():
        return

    subprocess.run(
        ["dbt", "deps", "--project-dir", str(DBT_PROJECT_DIR)],
        check=True,
        capture_output=True,
        text=True,
    )


_ensure_packages_installed()

dbt_project = DbtProject(
    project_dir=DBT_PROJECT_DIR,
    target=DBT_TARGET,
)

# Regenerates target/manifest.json when running under `dagster dev`, and does
# nothing in a deployment. The manifest is what Dagster reads to build the
# asset graph, so without this a model added since the last parse is invisible
# in the UI while still being built by `dbt build` — the graph and the run
# disagree, and the graph is the thing people trust.
dbt_project.prepare_if_dev()
