"""Seed the system category taxonomy.

Idempotent: keyed on slug, so re-running updates names/icons/ordering in place
and never duplicates or orphans a category. Safe to run on every deploy.

    python -m app.seeds.categories
"""

import logging
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import Category
from app.seeds.taxonomy import TAXONOMY, child_slug
from app.services.provider_categories import DETAILED_TO_SLUG, PRIMARY_TO_SLUG

logger = logging.getLogger(__name__)


def all_slugs() -> set[str]:
    slugs: set[str] = set()
    for parent_slug, _, _, _, children in TAXONOMY:
        slugs.add(parent_slug)
        slugs.update(child_slug(parent_slug, name) for name in children)
    return slugs


def validate_provider_mapping() -> list[str]:
    """Catch typos in the Plaid mapping before they become silent misfiles.

    A mapping target that does not exist in the taxonomy would fail to resolve
    at runtime and quietly drop the transaction into Uncategorized — a bug that
    is invisible until someone audits a month of spend.
    """
    known = all_slugs()
    return sorted(
        {
            slug
            for slug in (*DETAILED_TO_SLUG.values(), *PRIMARY_TO_SLUG.values())
            if slug not in known
        }
    )


def seed_categories(db: Session) -> dict[str, int]:
    existing = {
        c.slug: c
        for c in db.scalars(select(Category).where(Category.user_id.is_(None))).all()
    }
    created = updated = 0

    for parent_order, (slug, name, kind, icon, children) in enumerate(TAXONOMY):
        parent = existing.get(slug)
        if parent is None:
            parent = Category(slug=slug, user_id=None)
            db.add(parent)
            created += 1
        else:
            updated += 1

        parent.name = name
        parent.kind = kind.value
        parent.icon = icon
        parent.parent_id = None
        parent.sort_order = parent_order
        parent.is_system = True
        db.flush()

        for child_order, child_name in enumerate(children):
            cslug = child_slug(slug, child_name)
            child = existing.get(cslug)
            if child is None:
                child = Category(slug=cslug, user_id=None)
                db.add(child)
                created += 1
            else:
                updated += 1

            child.name = child_name
            # Children inherit their parent's money semantics. Allowing a child
            # to differ would let an EXPENSE child hide under a TRANSFER parent
            # and silently escape budget exclusion.
            child.kind = kind.value
            child.icon = None
            child.parent_id = parent.id
            child.sort_order = child_order
            child.is_system = True
            db.flush()

    db.commit()
    return {"created": created, "updated": updated}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    broken = validate_provider_mapping()
    if broken:
        logger.error("Plaid mapping references %d unknown slugs:", len(broken))
        for slug in broken:
            logger.error("  - %s", slug)
        return 1

    with SessionLocal() as db:
        counts = seed_categories(db)
        rows = db.scalars(select(Category).where(Category.user_id.is_(None))).all()

    parents = sum(1 for c in rows if c.parent_id is None)
    logger.info(
        "Taxonomy seeded: %d created, %d updated -> %d categories (%d parents, %d children)",
        counts["created"], counts["updated"], len(rows), parents, len(rows) - parents,
    )
    logger.info(
        "Plaid mapping: %d detailed + %d primary targets all resolve.",
        len(DETAILED_TO_SLUG), len(PRIMARY_TO_SLUG),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
