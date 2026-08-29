"""SEC/XBRL fundamentals as the second root asset.

This is the only asset in the project that CREATES data rather than reshaping
it. `app.services.research.analyze()` fetches EDGAR, caches in Redis and
returns a transient report — it writes nothing, and there is no filings table
in the OLTP schema. So there is no Postgres data for dbt to read here until
this asset lands some.

Calls the existing, tested `analyze()` rather than reimplementing the XBRL
parser. That function is 1,200 lines of concept stitching, split
renormalisation and sector caveats; a second copy in the analytics layer would
be a second thing to keep correct, and it would drift.

The tickers are not a hardcoded list. They are the equities actually held in
the portfolio, read from the OLTP `security` table — which is what makes
`dim_companies.ticker` a genuine conformed dimension against
`dim_securities.ticker_symbol` rather than two unrelated tables that happen to
share a column name.
"""

from pathlib import Path

from dagster import (
    AssetExecutionContext,
    Config,
    Failure,
    MetadataValue,
    asset,
)

from .resources import OltpDatabase

LANDING_DDL = Path(__file__).resolve().parent.parent / "ddl" / "landing.sql"

# Only EQUITY. An ETF or a mutual fund resolves to a CIK and files with the SEC,
# but it files N-CSR and N-PORT rather than the 10-K financial concepts this
# framework reads, so `analyze()` would raise FilingNotAvailable for every one
# of them. Filtering here means the skip count reports companies that genuinely
# have no annual data, rather than being dominated by funds that were never
# going to have any.
RESEARCHABLE_SECURITY_TYPE = "EQUITY"


class ResearchSnapshotConfig(Config):
    """Run-time overrides, all optional.

    `tickers` exists so the asset can be pointed at one company from the UI
    while debugging without waiting on the whole portfolio. Empty means "read
    them from the portfolio", which is the scheduled behaviour.
    """

    tickers: list[str] = []
    # A ceiling, not a target. Each ticker costs a companyfacts document (tens
    # of MB for a large filer), a 10-K and a proxy, all through a 6 req/s
    # limiter — so an unbounded portfolio could turn a nightly refresh into an
    # hour of EDGAR traffic. Ten is far above the demo's two.
    max_tickers: int = 10


@asset(
    name="research_snapshot",
    group_name="ingestion",
    description=(
        "Run the four-stage research framework for every equity held, and land "
        "one dated snapshot of its fundamentals and check verdicts. Idempotent: "
        "fundamentals upsert on (ticker, fiscal period), checks on the day."
    ),
    compute_kind="sec",
    # SEC bans by IP, and `sec_rate_limit_per_second` in the app throttles
    # PER PROCESS. Dagster's default multiprocess executor would run this asset
    # in its own process alongside others, and any future partitioning of it
    # would produce N processes each believing it alone owns the 6 req/s budget.
    # A pool limit of 1 keeps that arithmetic true no matter what executor a
    # later change picks — the job also pins in_process, and this is the belt
    # that survives someone removing the braces.
    pool="sec_edgar",
)
def research_snapshot(
    context: AssetExecutionContext, config: ResearchSnapshotConfig, oltp: OltpDatabase
):
    # Lazy, for the same reason as plaid_sync: app.core.config requires
    # ENCRYPTION_KEY and the PLAID_* variables to construct, and at module scope
    # a missing one would take the whole code location offline — every dbt asset
    # included — rather than reddening the single asset that needs it.
    from app.services import research, sec_client
    from app.models import Security
    from sqlalchemy import select, text

    from .research_landing import ensure_landing_schema, write_report

    with oltp.session() as db:
        ensure_landing_schema(db, LANDING_DDL)

        if config.tickers:
            tickers = [t.strip().upper() for t in config.tickers if t.strip()]
        else:
            # Distinct because the same ticker can appear as a separate
            # `security` row per institution — Plaid issues its own security id
            # per item, so a portfolio holding AAPL at two brokerages has two
            # rows. Researching it twice would double the EDGAR traffic to land
            # a byte-identical snapshot.
            tickers = list(
                db.scalars(
                    select(Security.ticker_symbol)
                    .where(
                        Security.ticker_symbol.is_not(None),
                        Security.type == RESEARCHABLE_SECURITY_TYPE,
                    )
                    .distinct()
                    .order_by(Security.ticker_symbol)
                ).all()
            )

        tickers = tickers[: config.max_tickers]

        researched = 0
        fundamental_rows = 0
        check_rows = 0
        skipped: dict[str, int] = {}
        failures: list[str] = []

        for ticker in tickers:
            try:
                report = research.analyze(db, ticker)
            except (sec_client.TickerNotFound, sec_client.FilingNotAvailable) as exc:
                # Not a pipeline failure. A holding with no annual XBRL data —
                # a recent listing, a foreign private issuer, a fund that slipped
                # the type filter — is a fact about the company, not a broken
                # sync, and failing the run would mean one such holding blocks
                # the warehouse forever.
                skipped[type(exc).__name__] = skipped.get(type(exc).__name__, 0) + 1
                continue
            except sec_client.SECError as exc:
                # This one IS a failure: a 429, a 503, or an unreachable host.
                # Landing nothing and reporting success would leave the research
                # marts silently pinned to yesterday while every check went
                # green — the same silent-staleness failure plaid_sync guards
                # against, arriving through a different door.
                failures.append(type(exc).__name__)
                context.log.error("SEC request failed for a ticker: %s", exc)
                continue

            counts = write_report(db, report)
            researched += 1
            fundamental_rows += counts["fundamentals"]
            check_rows += counts["checks"]

        db.commit()

        # Read back rather than trusting the loop's own arithmetic. The counters
        # above say what this run intended to write; this says what is actually
        # in the table for dbt to read, and a silent rollback or a conflicting
        # upsert would show up as a divergence between the two.
        landed_companies = db.execute(
            text("SELECT count(*) FROM analytics_landing.research_company")
        ).scalar_one()

    context.add_output_metadata(
        {
            # Counts and error class names only. No revenue, no market cap, no
            # check verdicts — the Dagster event log is a SQLite file on a
            # volume with none of the protections the database has. That these
            # are public-company numbers rather than the user's does not change
            # the rule; it just lowers the stakes of breaking it.
            "companies_researched": researched,
            "companies_skipped": MetadataValue.json(skipped),
            "fundamental_rows_written": fundamental_rows,
            "check_rows_written": check_rows,
            "companies_in_landing": landed_companies,
        }
    )

    if failures:
        by_class: dict[str, int] = {}
        for name in failures:
            by_class[name] = by_class.get(name, 0) + 1
        raise Failure(
            description=(
                f"{len(failures)} of {len(tickers)} tickers failed against SEC EDGAR"
            ),
            metadata={"error_classes": MetadataValue.json(by_class)},
        )
