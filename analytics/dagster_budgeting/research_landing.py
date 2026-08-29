"""Writing a ResearchReport into the landing schema, idempotently.

Separate from the asset so the SQL can be tested without Dagster's execution
machinery in the way, and so the asset body stays a readable description of
what happens rather than a wall of column lists.

Every statement here is parameterised. That is CLAUDE.md rule 5 and it is not
decorative in this file: `company_name`, `fiscal_label` and `check_label` are
strings that came off a third-party document, and an f-string would put an
SEC-controlled value into the query text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Columns of analytics_landing.research_fundamentals that come straight off an
# AnnualRow, in one place so the INSERT, the placeholder list and the DO UPDATE
# cannot drift apart. Adding a measure means adding it here and in the DDL, and
# nowhere else.
_FUNDAMENTAL_MEASURES = (
    "revenue_cents",
    "gross_profit_cents",
    "gross_margin_bps",
    "operating_income_cents",
    "operating_margin_bps",
    "net_income_cents",
    "net_margin_bps",
    "diluted_shares",
    "diluted_eps",
    "operating_cash_flow_cents",
    "capex_cents",
    "free_cash_flow_cents",
    "cash_and_sti_cents",
    "total_debt_cents",
    "long_term_debt_cents",
    "equity_cents",
    "total_liabilities_cents",
    "interest_expense_cents",
)


def ensure_landing_schema(db: Session, ddl_path: Path) -> None:
    """Apply ddl/landing.sql. Idempotent, and run on every materialisation.

    Deliberately not a one-shot bootstrap step. A schema that only exists if
    someone remembered to run a script is a schema that is missing on the one
    machine where it matters, and the failure — dbt reporting a source table
    that does not exist — points nowhere near the cause. Every statement in the
    file is IF NOT EXISTS, so paying for it on each run costs a few
    milliseconds and removes an ordering dependency entirely.
    """
    db.execute(text(ddl_path.read_text()))


def _company_sql() -> str:
    # first_snapshot_date uses LEAST against the existing row so a re-run can
    # only ever move it earlier, never forward. snapshot_count increments only
    # when the date actually changes, so running the asset twice in one day
    # does not inflate it — the count means "days observed", which is what makes
    # it usable as a staleness signal.
    return """
        INSERT INTO analytics_landing.research_company AS c (
            ticker, cik, company_name, last_snapshot_date, first_snapshot_date,
            snapshot_count, updated_at
        )
        VALUES (:ticker, :cik, :company_name, :snapshot_date, :snapshot_date, 1, now())
        ON CONFLICT (ticker) DO UPDATE SET
            cik                 = EXCLUDED.cik,
            company_name        = EXCLUDED.company_name,
            last_snapshot_date  = GREATEST(c.last_snapshot_date, EXCLUDED.last_snapshot_date),
            first_snapshot_date = LEAST(c.first_snapshot_date, EXCLUDED.first_snapshot_date),
            snapshot_count      = c.snapshot_count
                                  + CASE WHEN EXCLUDED.last_snapshot_date > c.last_snapshot_date
                                         THEN 1 ELSE 0 END,
            updated_at          = now()
    """


def _fundamentals_sql() -> str:
    columns = ", ".join(_FUNDAMENTAL_MEASURES)
    placeholders = ", ".join(f":{name}" for name in _FUNDAMENTAL_MEASURES)
    updates = ",\n            ".join(
        f"{name} = EXCLUDED.{name}" for name in _FUNDAMENTAL_MEASURES
    )
    # Overwrites on conflict rather than leaving the first reading in place.
    # A fiscal year's figures do change — restatements, and this framework's own
    # split renormalisation — and the newer reading is the better one. Keeping
    # the older would mean a stale FY2023 survived forever because it happened
    # to be seen first.
    return f"""
        INSERT INTO analytics_landing.research_fundamentals (
            ticker, fiscal_period_end, fiscal_label, snapshot_date, cik,
            {columns}, updated_at
        )
        VALUES (
            :ticker, :fiscal_period_end, :fiscal_label, :snapshot_date, :cik,
            {placeholders}, now()
        )
        ON CONFLICT (ticker, fiscal_period_end) DO UPDATE SET
            fiscal_label  = EXCLUDED.fiscal_label,
            snapshot_date = EXCLUDED.snapshot_date,
            cik           = EXCLUDED.cik,
            {updates},
            updated_at    = now()
    """


def _checks_sql() -> str:
    # snapshot_date is in the key here, unlike fundamentals, so this accumulates
    # one row per company per day per check — the time series that makes a
    # PASS→FAIL flip visible. Re-running on the same day overwrites that day.
    return """
        INSERT INTO analytics_landing.research_check (
            ticker, snapshot_date, stage_key, check_key,
            stage_number, stage_title, stage_status,
            check_label, status, value_bps, value_cents, updated_at
        )
        VALUES (
            :ticker, :snapshot_date, :stage_key, :check_key,
            :stage_number, :stage_title, :stage_status,
            :check_label, :status, :value_bps, :value_cents, now()
        )
        ON CONFLICT (ticker, snapshot_date, stage_key, check_key) DO UPDATE SET
            stage_number = EXCLUDED.stage_number,
            stage_title  = EXCLUDED.stage_title,
            stage_status = EXCLUDED.stage_status,
            check_label  = EXCLUDED.check_label,
            status       = EXCLUDED.status,
            value_bps    = EXCLUDED.value_bps,
            value_cents  = EXCLUDED.value_cents,
            updated_at   = now()
    """


def write_report(db: Session, report: Any) -> dict[str, int]:
    """Land one ResearchReport. Returns row counts, not data.

    Does NOT commit — the caller owns the transaction, so a failure partway
    through a portfolio leaves the landing schema untouched rather than half
    updated with some companies snapshotted today and others last week.
    """
    snapshot_date = report.generated_at

    db.execute(
        text(_company_sql()),
        {
            "ticker": report.ticker,
            "cik": report.cik,
            "company_name": report.company_name,
            "snapshot_date": snapshot_date,
        },
    )

    fundamentals_sql = text(_fundamentals_sql())
    for row in report.annuals:
        params: dict[str, Any] = {
            "ticker": report.ticker,
            "fiscal_period_end": row.period_end,
            "fiscal_label": row.fiscal_label,
            "snapshot_date": snapshot_date,
            "cik": report.cik,
        }
        for name in _FUNDAMENTAL_MEASURES:
            params[name] = getattr(row, name)
        db.execute(fundamentals_sql, params)

    checks_sql = text(_checks_sql())
    check_count = 0
    for stage in report.stages:
        # stage.status is a derived property on the dataclass: worst verdict
        # wins and an UNKNOWN cannot be hidden by a PASS. Reading it once per
        # stage and stamping it on each check is what gives the dbt test both
        # the parts and the whole to compare.
        stage_status = stage.status
        for check in stage.checks:
            db.execute(
                checks_sql,
                {
                    "ticker": report.ticker,
                    "snapshot_date": snapshot_date,
                    "stage_key": stage.key,
                    "check_key": check.key,
                    "stage_number": stage.number,
                    "stage_title": stage.title,
                    "stage_status": stage_status,
                    "check_label": check.label,
                    "status": check.status,
                    "value_bps": check.value_bps,
                    "value_cents": check.value_cents,
                },
            )
            check_count += 1

    return {"fundamentals": len(report.annuals), "checks": check_count}
