"""phase 2 — merchants, taxonomy, rules, transfers, budgets

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27

The one destructive-looking step here is not destructive: Phase 1's
`transaction.category_id` was a VARCHAR holding Plaid's detailed PFC slug. It is
RENAMED to `provider_category` rather than dropped, because that slug is the
input to categorisation layer 3 — dropping it would mean every historical
transaction loses its only categorisation signal and could never be re-derived
without a full re-sync. The name `category_id` is then reused for the real
foreign key into the new taxonomy.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Trigram similarity backs fuzzy merchant deduplication.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ----------------------------------------------------------------- #
    # Category taxonomy
    # ----------------------------------------------------------------- #
    op.create_table(
        "category",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("category.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("icon", sa.String(48), nullable=True),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_category_user_id", "category", ["user_id"])
    op.create_index("ix_category_parent_id", "category", ["parent_id"])
    op.create_index("ix_category_kind", "category", ["kind"])
    op.create_index("ix_category_parent_sort", "category", ["parent_id", "sort_order"])
    # Partial uniques: NULL user_id (system categories) must still be unique by
    # slug, which a plain UNIQUE(user_id, slug) would not enforce.
    op.create_index(
        "uq_category_system_slug",
        "category",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "uq_category_user_slug",
        "category",
        ["user_id", "slug"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    # ----------------------------------------------------------------- #
    # Merchants
    # ----------------------------------------------------------------- #
    op.create_table(
        "merchant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("normalized_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column(
            "canonical_merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchant.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "default_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("category.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("link_method", sa.String(16), nullable=True),
        sa.Column("link_score", sa.Integer(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_merchant_normalized_key", "merchant", ["normalized_key"], unique=True)
    op.create_index("ix_merchant_canonical_merchant_id", "merchant", ["canonical_merchant_id"])
    op.create_index("ix_merchant_default_category_id", "merchant", ["default_category_id"])
    # GIN trigram index — turns the similarity() scan in the dedup path into an
    # index lookup once the merchant table stops being tiny.
    op.execute(
        "CREATE INDEX ix_merchant_normalized_key_trgm "
        "ON merchant USING gin (normalized_key gin_trgm_ops)"
    )

    # ----------------------------------------------------------------- #
    # Categorisation rules
    # ----------------------------------------------------------------- #
    op.create_table(
        "categorization_rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_type", sa.String(32), nullable=False),
        sa.Column("match_value", sa.Text(), nullable=True),
        sa.Column("amount_min_cents", sa.BigInteger(), nullable=True),
        sa.Column("amount_max_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "target_category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("category.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("applies_retroactively", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # A rule missing the fields its match_type reads would never fire and
        # would look like a categorisation bug. Reject it at write time instead.
        sa.CheckConstraint(
            "(match_type IN ('EXACT_MERCHANT', 'DESCRIPTION_CONTAINS', 'ACCOUNT_ID') "
            "  AND match_value IS NOT NULL AND length(trim(match_value)) > 0) "
            "OR (match_type = 'AMOUNT_EQUALS' AND amount_min_cents IS NOT NULL) "
            "OR (match_type = 'AMOUNT_RANGE' AND amount_min_cents IS NOT NULL "
            "  AND amount_max_cents IS NOT NULL AND amount_max_cents >= amount_min_cents)",
            name="ck_rule_match_fields_present",
        ),
    )
    op.create_index("ix_categorization_rule_user_id", "categorization_rule", ["user_id"])
    op.create_index(
        "ix_categorization_rule_target_category_id", "categorization_rule", ["target_category_id"]
    )
    op.create_index("ix_rule_user_priority", "categorization_rule", ["user_id", "priority"])

    # ----------------------------------------------------------------- #
    # Budgets
    # ----------------------------------------------------------------- #
    op.create_table(
        "budget",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("category.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("limit_cents", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(16), nullable=False, server_default="MONTHLY"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("rollover_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "category_id", name="uq_budget_user_category"),
        sa.CheckConstraint("limit_cents >= 0", name="ck_budget_limit_non_negative"),
    )
    op.create_index("ix_budget_user_id", "budget", ["user_id"])
    op.create_index("ix_budget_category_id", "budget", ["category_id"])

    # ----------------------------------------------------------------- #
    # Transaction: preserve the provider slug, then add the real FK
    # ----------------------------------------------------------------- #
    op.alter_column("transaction", "category_id", new_column_name="provider_category")
    op.execute("ALTER INDEX ix_transaction_category_id RENAME TO ix_transaction_provider_category")

    op.add_column(
        "transaction", sa.Column("normalized_key", sa.String(255), nullable=True)
    )
    op.add_column(
        "transaction",
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchant.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "transaction",
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("category.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("transaction", sa.Column("category_source", sa.String(16), nullable=True))
    op.add_column(
        "transaction",
        sa.Column(
            "transfer_pair_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transaction.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_index("ix_transaction_normalized_key", "transaction", ["normalized_key"])
    op.create_index("ix_transaction_merchant_id", "transaction", ["merchant_id"])
    op.create_index("ix_transaction_category_id", "transaction", ["category_id"])
    op.create_index("ix_transaction_category_source", "transaction", ["category_source"])
    op.create_index("ix_transaction_transfer_pair_id", "transaction", ["transfer_pair_id"])
    # The budget engine's hot path: spend for one category over a date window.
    op.create_index("ix_transaction_category_date", "transaction", ["category_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_transaction_category_date", table_name="transaction")
    op.drop_index("ix_transaction_transfer_pair_id", table_name="transaction")
    op.drop_index("ix_transaction_category_source", table_name="transaction")
    op.drop_index("ix_transaction_category_id", table_name="transaction")
    op.drop_index("ix_transaction_merchant_id", table_name="transaction")
    op.drop_index("ix_transaction_normalized_key", table_name="transaction")

    op.drop_column("transaction", "transfer_pair_id")
    op.drop_column("transaction", "category_source")
    op.drop_column("transaction", "category_id")
    op.drop_column("transaction", "merchant_id")
    op.drop_column("transaction", "normalized_key")

    op.execute("ALTER INDEX ix_transaction_provider_category RENAME TO ix_transaction_category_id")
    op.alter_column("transaction", "provider_category", new_column_name="category_id")

    op.drop_table("budget")
    op.drop_table("categorization_rule")
    op.execute("DROP INDEX IF EXISTS ix_merchant_normalized_key_trgm")
    op.drop_table("merchant")
    op.drop_table("category")
