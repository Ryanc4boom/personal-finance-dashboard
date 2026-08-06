"""phase 3 — recurring streams, subscriptions, cash flow forecasting

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-27

Purely additive. One new table plus one nullable FK on `transaction`, so this
migration is safe to run against a populated database and the downgrade is a
clean drop — no Phase 1/2 column is renamed, retyped or dropped.

`transaction.is_recurring` already existed (Phase 1, always false). Phase 3 gives
it a meaning: it is now the denormalised form of `recurring_stream_id IS NOT
NULL`, maintained by services/recurring.py.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recurring_stream",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "merchant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("merchant.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("category.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("normalized_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("expected_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("last_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("amount_variance_bps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("last_date", sa.Date(), nullable=False),
        sa.Column("next_expected_date", sa.Date(), nullable=False),
        sa.Column("median_interval_days", sa.Integer(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("status_source", sa.String(8), nullable=False, server_default="AUTO"),
        sa.Column("is_subscription", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "is_subscription_locked", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("expected_amount_cents > 0", name="ck_recurring_stream_amount_positive"),
        sa.CheckConstraint("occurrence_count >= 2", name="ck_recurring_stream_occurrences"),
        # Every column here is NOT NULL, so this genuinely constrains. A unique
        # index touching a nullable column would not: Postgres treats NULLs as
        # distinct and would happily admit duplicate streams.
        sa.UniqueConstraint(
            "user_id",
            "normalized_key",
            "frequency",
            "direction",
            name="uq_recurring_stream_identity",
        ),
    )
    op.create_index("ix_recurring_stream_user_id", "recurring_stream", ["user_id"])
    op.create_index("ix_recurring_stream_merchant_id", "recurring_stream", ["merchant_id"])
    op.create_index("ix_recurring_stream_category_id", "recurring_stream", ["category_id"])
    op.create_index("ix_recurring_stream_normalized_key", "recurring_stream", ["normalized_key"])
    op.create_index("ix_recurring_stream_direction", "recurring_stream", ["direction"])
    op.create_index(
        "ix_recurring_stream_next_expected_date", "recurring_stream", ["next_expected_date"]
    )
    # The forecaster's hot path: active streams due inside a horizon.
    op.create_index(
        "ix_recurring_stream_user_status_next",
        "recurring_stream",
        ["user_id", "status", "next_expected_date"],
    )

    op.add_column(
        "transaction",
        sa.Column("recurring_stream_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_transaction_recurring_stream",
        "transaction",
        "recurring_stream",
        ["recurring_stream_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_transaction_recurring_stream_id", "transaction", ["recurring_stream_id"]
    )
    # Detection groups by normalised merchant and walks the group in date order.
    op.create_index(
        "ix_transaction_normalized_key_date", "transaction", ["normalized_key", "date"]
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_normalized_key_date", table_name="transaction")
    op.drop_index("ix_transaction_recurring_stream_id", table_name="transaction")
    op.drop_constraint("fk_transaction_recurring_stream", "transaction", type_="foreignkey")
    op.drop_column("transaction", "recurring_stream_id")
    op.drop_table("recurring_stream")
