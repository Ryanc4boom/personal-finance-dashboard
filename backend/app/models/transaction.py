import uuid
from datetime import date as date_type

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Transaction(Base, TimestampMixin):
    """Normalised ledger row.

    Sign convention (enforced at ingestion, see services/normalize.py):
        amount_cents > 0  => money INTO the account (INFLOW)
        amount_cents < 0  => money OUT of the account (OUTFLOW)
    This is the inverse of Plaid's raw convention and is applied exactly once,
    at the ingestion boundary. Everything downstream can sum blindly.
    """

    __tablename__ = "transaction"
    __table_args__ = (
        Index("ix_transaction_account_date", "account_id", "date"),
        Index("ix_transaction_account_pending", "account_id", "is_pending"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_txn_id: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    # Set when this row was promoted from a pending transaction; retains the old
    # provider id so a replayed pending delta cannot resurrect the ghost row.
    pending_provider_txn_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )

    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False, index=True)

    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    posted_date: Mapped[date_type | None] = mapped_column(Date, nullable=True)

    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Phase 2: merchant normalisation ---------------------------------- #
    # Cached output of services/normalization.py. Denormalised onto the row so
    # the ledger can group and debug without joining through merchant.
    normalized_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchant.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Phase 2: categorisation ------------------------------------------ #
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("category.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Which layer produced `category_id`. Guards manual picks against being
    # overwritten by a later automated pass — see models/enums.CategorySource.
    category_source: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    # Plaid's own detailed PFC slug, kept verbatim as the layer-3 input. This is
    # the column Phase 1 called `category_id`.
    provider_category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    is_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_transfer: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_recurring: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # The opposite leg of an internal transfer, if one was detected. Set on both
    # rows so the link reads the same from either side.
    transfer_pair_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transaction.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Phase 3: recurring streams ---------------------------------------- #
    # The stream this row is an occurrence of, if detection or auto-linking
    # claimed it. `is_recurring` is the denormalised boolean form, kept so the
    # ledger can filter without a join.
    recurring_stream_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recurring_stream.id", ondelete="SET NULL"), nullable=True, index=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    excluded_from_budget: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    account: Mapped["Account"] = relationship(back_populates="transactions")  # noqa: F821
    category: Mapped["Category | None"] = relationship(lazy="joined")  # noqa: F821
    merchant: Mapped["Merchant | None"] = relationship(  # noqa: F821
        lazy="joined", foreign_keys=[merchant_id]
    )
