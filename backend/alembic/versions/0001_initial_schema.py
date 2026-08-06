"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-25

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "user",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("auth_hash", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    op.create_table(
        "institution",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_institution_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_institution_provider_institution_id",
        "institution",
        ["provider_institution_id"],
        unique=True,
    )

    op.create_table(
        "item",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_item_id", sa.String(length=128), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="good", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["institution_id"], ["institution.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_item_user_id", "item", ["user_id"])
    op.create_index("ix_item_institution_id", "item", ["institution_id"])
    op.create_index("ix_item_provider_item_id", "item", ["provider_item_id"], unique=True)

    op.create_table(
        "account",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_account_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("mask", sa.String(length=8), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("subtype", sa.String(length=64), nullable=True),
        sa.Column("current_balance_cents", sa.BigInteger(), nullable=True),
        sa.Column("available_balance_cents", sa.BigInteger(), nullable=True),
        sa.Column("credit_limit_cents", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "provider_account_id", name="uq_account_item_provider"),
    )
    op.create_index("ix_account_item_id", "account", ["item_id"])
    op.create_index("ix_account_provider_account_id", "account", ["provider_account_id"])
    op.create_index("ix_account_type", "account", ["type"])

    op.create_table(
        "transaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_txn_id", sa.String(length=128), nullable=False),
        sa.Column("pending_provider_txn_id", sa.String(length=128), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("posted_date", sa.Date(), nullable=True),
        sa.Column("description_raw", sa.Text(), nullable=False),
        sa.Column("merchant_name", sa.String(length=255), nullable=True),
        sa.Column("category_id", sa.String(length=64), nullable=True),
        sa.Column("is_pending", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_transfer", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_recurring", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("excluded_from_budget", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(direction = 'INFLOW' AND amount_cents >= 0) OR "
            "(direction = 'OUTFLOW' AND amount_cents <= 0)",
            name="ck_transaction_direction_sign",
        ),
    )
    op.create_index("ix_transaction_account_id", "transaction", ["account_id"])
    op.create_index("ix_transaction_provider_txn_id", "transaction", ["provider_txn_id"], unique=True)
    op.create_index("ix_transaction_pending_provider_txn_id", "transaction", ["pending_provider_txn_id"])
    op.create_index("ix_transaction_direction", "transaction", ["direction"])
    op.create_index("ix_transaction_date", "transaction", ["date"])
    op.create_index("ix_transaction_category_id", "transaction", ["category_id"])
    op.create_index("ix_transaction_account_date", "transaction", ["account_id", "date"])
    op.create_index("ix_transaction_account_pending", "transaction", ["account_id", "is_pending"])

    op.create_table(
        "raw_transaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), server_default="plaid", nullable=False),
        sa.Column("provider_txn_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raw_transaction_item_id", "raw_transaction", ["item_id"])
    op.create_index("ix_raw_transaction_provider_txn_id", "raw_transaction", ["provider_txn_id"])
    op.create_index("ix_raw_transaction_item_received", "raw_transaction", ["item_id", "received_at"])

    op.create_table(
        "balance_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", "date"),
        sa.UniqueConstraint("account_id", "date", name="uq_balance_snapshot_account_date"),
    )
    op.create_index("ix_balance_snapshot_account_id", "balance_snapshot", ["account_id"])

    # Timescale hypertable for time-series balance history. Non-fatal if the
    # extension is unavailable (e.g. plain postgres image) — the table still works.
    op.execute(
        """
        DO $$
        BEGIN
            PERFORM create_hypertable(
                'balance_snapshot', 'date',
                chunk_time_interval => INTERVAL '1 month',
                migrate_data => TRUE
            );
        EXCEPTION WHEN undefined_function THEN
            RAISE NOTICE 'TimescaleDB not available; balance_snapshot left as a plain table';
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("balance_snapshot")
    op.drop_table("raw_transaction")
    op.drop_table("transaction")
    op.drop_table("account")
    op.drop_table("item")
    op.drop_table("institution")
    op.drop_table("user")
