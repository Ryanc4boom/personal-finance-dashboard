"""The four-layer categorisation engine.

Layers run in descending authority and the first one to produce a category wins:

    1. USER RULES     explicit instructions, highest priority number first
    2. MERCHANT       the canonical merchant's default category
    3. PROVIDER       Plaid's PFC slug mapped into our taxonomy
    4. UNCATEGORIZED  the holding pen — never guessed, always explicit

One invariant sits above all four: a category with
`category_source = 'USER'` was chosen by a human and no automated pass may
overwrite it. Every write path here goes through `_may_overwrite`, and the only
way past it is an explicit `force=True` from a caller that knows the user asked
for it.

Rule predicates are defined once per match type in `_MATCHERS`, as a pair of a
Python callable (used per-transaction at ingestion) and a SQLAlchemy expression
(used to bulk-select rows for retroactive application). The two are declared
side by side precisely because they must agree — a divergence would mean a rule
that categorises new transactions differently from historical ones.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Category,
    CategorizationRule,
    Item,
    Merchant,
    Transaction,
    User,
)
from app.models.enums import OVERWRITABLE_SOURCES, CategorySource, RuleMatchType
from app.seeds.taxonomy import UNCATEGORIZED_SLUG
from app.services.normalization import normalized_key
from app.services.provider_categories import slug_for_provider_category

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolution:
    category_id: uuid.UUID | None
    source: str
    rule_id: uuid.UUID | None = None


# --------------------------------------------------------------------------- #
# Rule matching
# --------------------------------------------------------------------------- #

def _match_exact_merchant(rule: CategorizationRule, txn: Transaction) -> bool:
    # Compared on the *normalised* key, not the raw string, so a rule the user
    # created from "SQ *BLUE BOTTLE 0421 OAKLAND CA" also catches
    # "TST* BLUE BOTTLE COFFEE" — which is the whole point of normalisation.
    return bool(txn.normalized_key) and normalized_key(rule.match_value) == txn.normalized_key


def _sql_exact_merchant(rule: CategorizationRule):
    return Transaction.normalized_key == normalized_key(rule.match_value)


def _match_description_contains(rule: CategorizationRule, txn: Transaction) -> bool:
    needle = (rule.match_value or "").strip().lower()
    if not needle:
        return False
    haystacks = (txn.description_raw or "", txn.merchant_name or "")
    return any(needle in h.lower() for h in haystacks)


def _sql_description_contains(rule: CategorizationRule):
    # escape() keeps a literal % or _ in the user's text from turning into a
    # wildcard, which would silently over-match.
    pattern = f"%{(rule.match_value or '').strip()}%"
    return or_(
        Transaction.description_raw.ilike(pattern, escape="\\"),
        Transaction.merchant_name.ilike(pattern, escape="\\"),
    )


def _match_amount_equals(rule: CategorizationRule, txn: Transaction) -> bool:
    return abs(txn.amount_cents) == rule.amount_min_cents


def _sql_amount_equals(rule: CategorizationRule):
    return func.abs(Transaction.amount_cents) == rule.amount_min_cents


def _match_amount_range(rule: CategorizationRule, txn: Transaction) -> bool:
    return rule.amount_min_cents <= abs(txn.amount_cents) <= rule.amount_max_cents


def _sql_amount_range(rule: CategorizationRule):
    return func.abs(Transaction.amount_cents).between(
        rule.amount_min_cents, rule.amount_max_cents
    )


def _match_account(rule: CategorizationRule, txn: Transaction) -> bool:
    return str(txn.account_id) == (rule.match_value or "").strip()


def _sql_account(rule: CategorizationRule):
    try:
        account_id = uuid.UUID((rule.match_value or "").strip())
    except ValueError:
        # Unparseable account rule matches nothing rather than everything.
        return Transaction.id.is_(None)
    return Transaction.account_id == account_id


# Amounts are matched on the absolute value so a user writing "$9.99" does not
# have to know that outflows are stored negative.
_MATCHERS = {
    RuleMatchType.EXACT_MERCHANT.value: (_match_exact_merchant, _sql_exact_merchant),
    RuleMatchType.DESCRIPTION_CONTAINS.value: (
        _match_description_contains, _sql_description_contains
    ),
    RuleMatchType.AMOUNT_EQUALS.value: (_match_amount_equals, _sql_amount_equals),
    RuleMatchType.AMOUNT_RANGE.value: (_match_amount_range, _sql_amount_range),
    RuleMatchType.ACCOUNT_ID.value: (_match_account, _sql_account),
}


def rule_matches(rule: CategorizationRule, txn: Transaction) -> bool:
    matcher = _MATCHERS.get(rule.match_type)
    if matcher is None:
        logger.warning("rule %s has unknown match_type %s", rule.id, rule.match_type)
        return False
    return bool(matcher[0](rule, txn))


def rule_sql_filter(rule: CategorizationRule):
    matcher = _MATCHERS.get(rule.match_type)
    if matcher is None:
        return Transaction.id.is_(None)
    return matcher[1](rule)


# --------------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------------- #

class CategoryResolver:
    """Resolves categories for a batch of transactions.

    Rules and the slug lookup are loaded once and held for the resolver's life.
    A retroactive re-categorisation touches every transaction the user owns, so
    re-querying the rule set per row would turn one pass into tens of thousands
    of round trips.
    """

    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user

        self.rules: list[CategorizationRule] = list(
            db.scalars(
                select(CategorizationRule)
                .where(
                    CategorizationRule.user_id == user.id,
                    CategorizationRule.is_active.is_(True),
                )
                # Higher priority wins; newest breaks a tie. Declared in the
                # model docstring and depended on by the whole engine.
                .order_by(
                    CategorizationRule.priority.desc(),
                    CategorizationRule.created_at.desc(),
                )
            ).all()
        )

        categories = db.scalars(
            select(Category).where(
                or_(Category.user_id.is_(None), Category.user_id == user.id)
            )
        ).all()
        self._by_slug = {c.slug: c for c in categories}
        self._by_id = {c.id: c for c in categories}

        uncategorized = self._by_slug.get(UNCATEGORIZED_SLUG)
        if uncategorized is None:
            raise RuntimeError(
                "The 'uncategorized' category is missing. "
                "Run: python -m app.seeds.categories"
            )
        self.uncategorized_id = uncategorized.id

        self._merchant_cache: dict[uuid.UUID, uuid.UUID | None] = {}

    # -- layers ----------------------------------------------------------- #

    def _layer_1_rules(self, txn: Transaction) -> Resolution | None:
        for rule in self.rules:
            if rule.target_category_id not in self._by_id:
                continue
            if rule_matches(rule, txn):
                return Resolution(
                    rule.target_category_id, CategorySource.RULE.value, rule.id
                )
        return None

    def _layer_2_merchant(self, txn: Transaction) -> Resolution | None:
        if txn.merchant_id is None:
            return None

        if txn.merchant_id not in self._merchant_cache:
            merchant = self.db.get(Merchant, txn.merchant_id)
            if merchant is not None and merchant.canonical_merchant_id is not None:
                merchant = self.db.get(Merchant, merchant.canonical_merchant_id) or merchant
            self._merchant_cache[txn.merchant_id] = (
                merchant.default_category_id if merchant else None
            )

        category_id = self._merchant_cache[txn.merchant_id]
        if category_id is None or category_id not in self._by_id:
            return None
        return Resolution(category_id, CategorySource.MERCHANT.value)

    def _layer_3_provider(self, txn: Transaction) -> Resolution | None:
        slug = slug_for_provider_category(txn.provider_category)
        if slug is None:
            return None
        category = self._by_slug.get(slug)
        if category is None:
            return None
        return Resolution(category.id, CategorySource.PROVIDER.value)

    def resolve(self, txn: Transaction) -> Resolution:
        return (
            self._layer_1_rules(txn)
            or self._layer_2_merchant(txn)
            or self._layer_3_provider(txn)
            or Resolution(self.uncategorized_id, CategorySource.UNCATEGORIZED.value)
        )

    def category_of(self, category_id: uuid.UUID | None) -> Category | None:
        return self._by_id.get(category_id) if category_id else None


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #

def _may_overwrite(txn: Transaction, force: bool) -> bool:
    """A human's pick is never clobbered by an automated pass."""
    if force:
        return True
    if txn.category_id is None:
        return True
    return txn.category_source in OVERWRITABLE_SOURCES


def apply_to_transaction(
    txn: Transaction, resolver: CategoryResolver, force: bool = False
) -> bool:
    """Categorise one transaction. Returns True if the row changed."""
    if not _may_overwrite(txn, force):
        return False

    resolution = resolver.resolve(txn)
    if txn.category_id == resolution.category_id and txn.category_source == resolution.source:
        return False

    txn.category_id = resolution.category_id
    txn.category_source = resolution.source
    return True


def set_category_manually(txn: Transaction, category_id: uuid.UUID) -> None:
    """Record a human's explicit pick, immune to later automated passes."""
    txn.category_id = category_id
    txn.category_source = CategorySource.USER.value


def user_transactions(user: User) -> Select:
    return (
        select(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(Item.user_id == user.id)
    )


def recategorize_all(
    db: Session, user: User, force: bool = False, only_uncategorized: bool = False
) -> dict:
    """Re-run the full engine over a user's history.

    Used after rules change in bulk or after the merchant table gains defaults.
    """
    resolver = CategoryResolver(db, user)
    stmt = user_transactions(user)
    if only_uncategorized:
        stmt = stmt.where(
            or_(
                Transaction.category_id.is_(None),
                Transaction.category_id == resolver.uncategorized_id,
            )
        )

    changed = skipped = 0
    for txn in db.scalars(stmt).all():
        if not _may_overwrite(txn, force):
            skipped += 1
            continue
        if apply_to_transaction(txn, resolver, force=force):
            changed += 1

    db.commit()
    return {"changed": changed, "skipped_user_categorized": skipped}


def apply_rule_retroactively(
    db: Session, user: User, rule: CategorizationRule, force: bool = False
) -> dict:
    """Apply one rule across the user's existing transactions.

    Rows the user categorised by hand are left alone and counted separately, so
    the UI can say "12 updated, 3 manual picks kept" instead of silently
    destroying deliberate work. `force=True` overrides that, for a caller that
    has asked the user first.

    Note this applies *this* rule, not the whole engine: a higher-priority rule
    that would have won is not consulted, because the user's intent here is
    "make these transactions this category".
    """
    if not rule.is_active:
        return {"matched": 0, "changed": 0, "skipped_user_categorized": 0}

    stmt = user_transactions(user).where(rule_sql_filter(rule))
    matched = changed = skipped = 0

    for txn in db.scalars(stmt).all():
        matched += 1
        if not _may_overwrite(txn, force):
            skipped += 1
            continue
        if txn.category_id != rule.target_category_id or (
            txn.category_source != CategorySource.RULE.value
        ):
            txn.category_id = rule.target_category_id
            txn.category_source = CategorySource.RULE.value
            changed += 1

    db.commit()
    return {"matched": matched, "changed": changed, "skipped_user_categorized": skipped}
