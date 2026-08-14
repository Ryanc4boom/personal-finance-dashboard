"""Delete linked Items and everything ingested under them.

Written for the sandbox -> production cutover: the database holds Plaid sandbox
Items and seeded demo Items whose balances are fake. Left in place they do not
merely look untidy — net worth, budget pacing and forecasting all sum across
every account, so fake money silently corrupts real numbers.

Deleting is deliberately explicit. Nothing is selected by heuristic, because
there is no column that distinguishes a sandbox Item from a production one and
guessing wrong destroys real financial history. You name the Items; the script
shows you exactly what would go, and only touches the database once you pass
--yes.

    python scripts/purge_items.py                          # list, delete nothing
    python scripts/purge_items.py --item DEMO_ITEM         # dry run
    python scripts/purge_items.py --item DEMO_ITEM --yes   # actually delete
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402

# Rows that disappear on their own via ON DELETE CASCADE once the Item goes.
# Listed here purely so the dry run can report the true blast radius: a bare
# "1 item" is not informed consent when 545 transactions hang off it.
CASCADE_COUNTS = {
    "accounts": "select count(*) from account where item_id = :id",
    "transactions": """select count(*) from transaction t join account a on a.id = t.account_id
                       where a.item_id = :id""",
    "raw payloads": "select count(*) from raw_transaction where item_id = :id",
    "holdings": """select count(*) from holding h join account a on a.id = h.account_id
                   where a.item_id = :id""",
    "investment txns": """select count(*) from investment_transaction i
                          join account a on a.id = i.account_id where a.item_id = :id""",
    "balance snapshots": """select count(*) from balance_snapshot b join account a on a.id = b.account_id
                            where a.item_id = :id""",
}

# User-scoped derived tables. These do NOT cascade off an Item, so after a purge
# they still describe accounts that no longer exist. Both are recomputable from
# transactions and balances, unlike budgets and goals, which the user authored by
# hand and which this script must never touch.
DERIVED = {
    "recurring_stream": "recurring streams (re-detected on next sync)",
    "net_worth_snapshot": "net worth history (recomputed going forward)",
}


def load_items(session):
    return list(
        session.execute(
            text(
                """
                select i.id, i.provider_item_id, coalesce(ins.name, '(unknown)') as institution,
                       i.created_at
                from item i
                left join institution ins on ins.id = i.institution_id
                order by i.created_at
                """
            )
        )
    )


def describe(session, item) -> list[str]:
    lines = []
    for label, sql in CASCADE_COUNTS.items():
        count = session.execute(text(sql), {"id": item.id}).scalar() or 0
        if count:
            lines.append(f"{count} {label}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--item",
        action="append",
        default=[],
        metavar="ID",
        help="provider_item_id or uuid to delete; repeatable",
    )
    parser.add_argument(
        "--reset-derived",
        action="store_true",
        help="also clear recurring streams and net worth history (leaves budgets and goals alone)",
    )
    parser.add_argument("--yes", action="store_true", help="perform the delete")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        items = load_items(session)
        if not items:
            print("No linked items.")
            return 0

        wanted = {w.lower() for w in args.item}
        selected = [
            i
            for i in items
            if i.provider_item_id.lower() in wanted or str(i.id).lower() in wanted
        ]

        print(f"{'':2}{'PROVIDER ITEM ID':<26} {'INSTITUTION':<20} LINKED     CONTENTS")
        for item in items:
            mark = "->" if item in selected else "  "
            contents = ", ".join(describe(session, item)) or "empty"
            print(
                f"{mark}{item.provider_item_id[:26]:<26} {item.institution[:20]:<20} "
                f"{item.created_at:%Y-%m-%d} {contents}"
            )

        unmatched = wanted - {i.provider_item_id.lower() for i in selected} - {
            str(i.id).lower() for i in selected
        }
        for miss in sorted(unmatched):
            print(f"\nNo item matches {miss!r} — nothing selected for it.")

        if not selected:
            print("\nNothing selected. Pass --item to choose what to delete.")
            return 1

        if not args.yes:
            print(f"\nDry run — {len(selected)} item(s) above marked '->' would be deleted.")
            print("Re-run with --yes to perform it.")
            return 0

        for item in selected:
            session.execute(text("delete from item where id = :id"), {"id": item.id})
            print(f"deleted {item.provider_item_id} ({item.institution})")

        if args.reset_derived:
            for table, label in DERIVED.items():
                deleted = session.execute(text(f"delete from {table}")).rowcount
                print(f"cleared {deleted} rows of {label}")

        session.commit()
        print("\nDone. Budgets and goals were left untouched.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
