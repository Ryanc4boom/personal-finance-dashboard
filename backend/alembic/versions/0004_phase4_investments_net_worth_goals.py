"""phase 4 — investments, portfolio analytics, net worth snapshots, financial goals

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-06

Purely additive: six new tables, no Phase 1-3 column renamed, retyped or
dropped, so this is safe against a populated database and the downgrade is a
clean drop.

Two tables are time series and become Timescale hypertables, matching the
treatment `balance_snapshot` got in 0001:

* `holding` — one row per position per day, so market movement is reconstructable.
* `net_worth_snapshot` — one row per user per day.

Timescale requires the partitioning column in every unique index, which is why
both carry a composite primary key of (id, <date column>) rather than the plain
`id` used everywhere else. As in 0001, hypertable creation is non-fatal: on a
plain Postgres image the tables still work, just unpartitioned.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----------------------------------------------------------------------- #
    # security — global instrument registry, shared across accounts and users
    # ----------------------------------------------------------------------- #
    op.create_table(
        "security",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_security_id", sa.String(128), nullable=False),
        sa.Column("ticker_symbol", sa.String(32), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("cusip", sa.String(16), nullable=True),
        sa.Column("isin", sa.String(16), nullable=True),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("asset_class", sa.String(24), nullable=False),
        sa.Column("asset_class_locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("close_price_cents", sa.BigInteger(), nullable=True),
        sa.Column("close_price_as_of", sa.Date(), nullable=True),
        sa.Column("previous_close_price_cents", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("is_cash_equivalent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # The provider's opaque id, not the ticker: tickers are reused after
        # delistings and are absent entirely for many funds and all cash rows.
        sa.UniqueConstraint("provider_security_id", name="uq_security_provider_id"),
    )
    op.create_index("ix_security_provider_security_id", "security", ["provider_security_id"])
    op.create_index("ix_security_ticker_symbol", "security", ["ticker_symbol"])
    op.create_index("ix_security_type", "security", ["type"])
    op.create_index("ix_security_asset_class", "security", ["asset_class"])

    # ----------------------------------------------------------------------- #
    # holding — dated position snapshots (hypertable on as_of_date)
    # ----------------------------------------------------------------------- #
    op.create_table(
        "holding",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Numeric, never float: fractional shares and crypto need exact decimals
        # for the same reason money is integer cents.
        sa.Column("quantity", sa.Numeric(28, 8), nullable=False),
        sa.Column("institution_price_cents", sa.BigInteger(), nullable=True),
        sa.Column("institution_value_cents", sa.BigInteger(), nullable=False),
        # Nullable and genuinely unknown after an ACATS transfer. Defaulting to
        # zero would report the whole position as gain.
        sa.Column("cost_basis_cents", sa.BigInteger(), nullable=True),
        sa.Column("value_drift_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", "as_of_date"),
        sa.UniqueConstraint(
            "account_id", "security_id", "as_of_date", name="uq_holding_account_security_date"
        ),
        sa.CheckConstraint("cost_basis_cents >= 0", name="ck_holding_cost_basis_non_negative"),
    )
    op.create_index("ix_holding_account_id", "holding", ["account_id"])
    op.create_index("ix_holding_security_id", "holding", ["security_id"])
    op.create_index("ix_holding_account_as_of", "holding", ["account_id", "as_of_date"])
    op.create_index("ix_holding_security_as_of", "holding", ["security_id", "as_of_date"])

    op.execute(
        """
        DO $$
        BEGIN
            PERFORM create_hypertable(
                'holding', 'as_of_date',
                chunk_time_interval => INTERVAL '3 months',
                migrate_data => TRUE
            );
        EXCEPTION WHEN undefined_function THEN
            RAISE NOTICE 'TimescaleDB not available; holding left as a plain table';
        END
        $$;
        """
    )

    # ----------------------------------------------------------------------- #
    # investment_transaction — trades and distributions, kept out of `transaction`
    # ----------------------------------------------------------------------- #
    op.create_table(
        "investment_transaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_investment_txn_id", sa.String(128), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("subtype", sa.String(64), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 8), nullable=True),
        sa.Column("price_cents", sa.BigInteger(), nullable=True),
        sa.Column("fees_cents", sa.BigInteger(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["security_id"], ["security.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "provider_investment_txn_id", name="uq_investment_transaction_provider_id"
        ),
    )
    op.create_index("ix_investment_transaction_account_id", "investment_transaction", ["account_id"])
    op.create_index("ix_investment_transaction_security_id", "investment_transaction", ["security_id"])
    op.create_index("ix_investment_transaction_type", "investment_transaction", ["type"])
    op.create_index("ix_investment_transaction_date", "investment_transaction", ["date"])
    op.create_index(
        "ix_investment_transaction_provider_id", "investment_transaction", ["provider_investment_txn_id"]
    )
    op.create_index(
        "ix_investment_transaction_account_date", "investment_transaction", ["account_id", "date"]
    )
    op.create_index(
        "ix_investment_transaction_security_date", "investment_transaction", ["security_id", "date"]
    )

    # ----------------------------------------------------------------------- #
    # net_worth_snapshot — daily assets/liabilities (hypertable on snapshot_date)
    # ----------------------------------------------------------------------- #
    op.create_table(
        "net_worth_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("total_assets_cents", sa.BigInteger(), nullable=False),
        # Positive magnitude; the sign lives in the subtraction below.
        sa.Column("total_liabilities_cents", sa.BigInteger(), nullable=False),
        sa.Column("net_worth_cents", sa.BigInteger(), nullable=False),
        sa.Column("breakdown_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(16), nullable=False, server_default="RECONSTRUCTED"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", "snapshot_date"),
        sa.UniqueConstraint("user_id", "snapshot_date", name="uq_net_worth_snapshot_user_date"),
        # The identity that makes the stored total safe to read directly.
        sa.CheckConstraint(
            "net_worth_cents = total_assets_cents - total_liabilities_cents",
            name="ck_net_worth_snapshot_balances",
        ),
        sa.CheckConstraint(
            "total_liabilities_cents >= 0", name="ck_net_worth_snapshot_liabilities_positive"
        ),
    )
    op.create_index("ix_net_worth_snapshot_user_id", "net_worth_snapshot", ["user_id"])

    op.execute(
        """
        DO $$
        BEGIN
            PERFORM create_hypertable(
                'net_worth_snapshot', 'snapshot_date',
                chunk_time_interval => INTERVAL '1 year',
                migrate_data => TRUE
            );
        EXCEPTION WHEN undefined_function THEN
            RAISE NOTICE 'TimescaleDB not available; net_worth_snapshot left as a plain table';
        END
        $$;
        """
    )

    # ----------------------------------------------------------------------- #
    # financial_goal (+ account link table)
    # ----------------------------------------------------------------------- #
    op.create_table(
        "financial_goal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("target_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("current_amount_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("monthly_contribution_cents", sa.BigInteger(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.CheckConstraint("target_amount_cents > 0", name="ck_financial_goal_target_positive"),
        sa.CheckConstraint(
            "current_amount_cents >= 0", name="ck_financial_goal_current_non_negative"
        ),
    )
    op.create_index("ix_financial_goal_user_id", "financial_goal", ["user_id"])
    op.create_index("ix_financial_goal_category", "financial_goal", ["category"])
    op.create_index("ix_financial_goal_target_date", "financial_goal", ["target_date"])
    op.create_index("ix_financial_goal_user_archived", "financial_goal", ["user_id", "is_archived"])

    # A join table rather than a UUID[] column on financial_goal: an array
    # cannot carry a foreign key, so a deleted account would leave a dangling
    # id behind in every goal that referenced it.
    op.create_table(
        "financial_goal_account",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["financial_goal.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("goal_id", "account_id", name="uq_financial_goal_account"),
    )
    op.create_index("ix_financial_goal_account_goal_id", "financial_goal_account", ["goal_id"])
    op.create_index("ix_financial_goal_account_account_id", "financial_goal_account", ["account_id"])


def downgrade() -> None:
    op.drop_table("financial_goal_account")
    op.drop_table("financial_goal")
    op.drop_table("net_worth_snapshot")
    op.drop_table("investment_transaction")
    op.drop_table("holding")
    op.drop_table("security")
