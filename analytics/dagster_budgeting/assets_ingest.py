"""Plaid ingestion as the root asset of the pipeline.

Calls `app.services.ingestion.sync_item` directly rather than POSTing to
`/api/v1/plaid/sync`. Going through HTTP would mean holding the API key in a
second place, consuming the route's Plaid rate-limit bucket, and having the
orchestrator depend on the web process being up to refresh a warehouse that
does not otherwise need it. The service layer is the actual unit of work; the
route is one caller of it and this is another.
"""

from dagster import (
    AssetExecutionContext,
    Failure,
    MetadataValue,
    asset,
)

from .resources import OltpDatabase

# Seeded fixtures carry a placeholder access token that was never Fernet
# encrypted, so `decrypt()` on one raises rather than returning something Plaid
# would reject. Skipping them by prefix is what lets `make demo` run the real
# orchestration end to end against synthetic data — the alternative is a demo
# that either crashes on its first asset or quietly excludes ingestion from the
# thing it claims to demonstrate.
DEMO_ITEM_PREFIX = "DEMO_"


@asset(
    name="plaid_sync",
    group_name="ingestion",
    description=(
        "Pull new and changed transactions, balances and holdings from Plaid "
        "for every linked item. Idempotent: re-running syncs from the stored "
        "cursor and upserts on the provider's ids."
    ),
    compute_kind="plaid",
)
def plaid_sync(context: AssetExecutionContext, oltp: OltpDatabase):
    # Imported inside the function on purpose. These modules pull in
    # `app.core.config`, which requires ENCRYPTION_KEY, PLAID_* and a database
    # URL to construct. At module scope a missing variable would break the
    # import and take the entire code location offline — including every dbt
    # asset, none of which need any of it. In here, the same mistake is one red
    # asset with a traceback naming the variable.
    from app.core.redis_client import sync_lock
    from app.models import Item
    from app.services import ingestion
    from sqlalchemy import select

    totals = {"added": 0, "modified": 0, "removed": 0, "promoted": 0}
    synced = skipped_demo = skipped_locked = 0
    failures: list[str] = []
    warnings: list[str] = []

    with oltp.session() as db:
        # Every item, across every user. This is the one place in the codebase
        # that legitimately operates outside a single user's scope — it is a
        # system job, not a request. The ownership rule it must still honour is
        # that the user is derived from the item (sync_item reads
        # `item.user_id`) and never supplied from outside; nothing here takes a
        # user id as input, so there is no way to point it at the wrong person.
        items = db.scalars(select(Item).order_by(Item.created_at)).all()

        for item in items:
            if (item.provider_item_id or "").startswith(DEMO_ITEM_PREFIX):
                skipped_demo += 1
                continue

            # The same Redis lock the HTTP route takes, for the same reason:
            # Plaid's cursor model is not concurrency-safe, and two syncs of one
            # item would replay the same delta from the same cursor. A webhook
            # firing while the daily schedule runs is exactly that race, and
            # this job is a new way to produce it.
            with sync_lock(str(item.id)) as acquired:
                if not acquired:
                    skipped_locked += 1
                    continue
                result = ingestion.sync_item(db, item)

            # THE line that stops this asset going green on a broken sync.
            #
            # sync_item RETURNS its errors — it sets result.error and returns a
            # SyncResult, rather than raising. An asset that ignored the return
            # value would report success for an item whose token was revoked,
            # whose institution was down, or which errored on page one, and the
            # dbt build downstream would then happily rebuild the marts from
            # stale data and pass every test. Silent, green, and wrong.
            if result.error:
                failures.append(result.error)
                continue

            synced += 1
            for key in totals:
                totals[key] += getattr(result, key)
            warnings.extend(result.warnings)

    context.add_output_metadata(
        {
            # Counts only. No item ids, no institution names, no amounts — the
            # Dagster event log is a SQLite file on a volume with none of the
            # protections the database has, and this repo's rule is that
            # verification is reported as counts rather than as data.
            "items_synced": synced,
            "items_skipped_demo": skipped_demo,
            "items_skipped_locked": skipped_locked,
            "transactions_added": totals["added"],
            "transactions_modified": totals["modified"],
            "transactions_removed": totals["removed"],
            "pending_promoted": totals["promoted"],
            "warnings": MetadataValue.json(warnings),
        }
    )

    if failures:
        # Plaid error codes are safe to surface — they name a failure mode
        # (ITEM_LOGIN_REQUIRED, RATE_LIMIT_EXCEEDED), not an account or a
        # balance. Counted by code so one dead item does not print N times.
        by_code: dict[str, int] = {}
        for code in failures:
            by_code[code] = by_code.get(code, 0) + 1
        raise Failure(
            description=f"{len(failures)} of {len(failures) + synced} items failed to sync",
            metadata={"error_codes": MetadataValue.json(by_code)},
        )
