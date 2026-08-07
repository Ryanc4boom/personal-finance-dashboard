import uuid
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Holding(Base):
    """One position in one account, as of one day.

    **This is a time series, not a mutable current-state row.** `as_of_date` is
    part of the identity, so every sync appends a new day rather than
    overwriting yesterday. Phase 1 made the same call for `balance_snapshot`,
    and for the same reason: without dated rows the net worth chart can only
    show contributions, never market movement, because there is nothing to
    compare a price against. Stored as a Timescale hypertable partitioned on
    `as_of_date`; Timescale requires the partitioning column in every unique
    index, hence the composite primary key.

    **Quantity is `Numeric`, not float.** Fractional shares are routine (0.4821
    of a share, 0.00381 BTC) and float would make a portfolio total drift in the
    last cents — the same reason money is integer cents everywhere else. Eight
    decimal places covers crypto satoshi precision.

    **Three money columns that should agree, and sometimes do not.**
    `institution_price_cents * quantity` is usually `institution_value_cents`,
    but not always: institutions round differently, and some report a stale
    price against a fresh value. The institution's own value is authoritative
    for totals — it is what the user sees on their statement — while price is
    kept for the per-share display. `value_drift_cents` records the discrepancy
    instead of hiding it, so a reconciliation bug is visible rather than
    silently absorbed into a portfolio total.
    """

    __tablename__ = "holding"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "security_id", "as_of_date", name="uq_holding_account_security_date"
        ),
        CheckConstraint("cost_basis_cents >= 0", name="ck_holding_cost_basis_non_negative"),
        # The dashboard's hot path: every position in an account on one day.
        Index("ix_holding_account_as_of", "account_id", "as_of_date"),
        # The backfill's hot path: one security's value across all of history.
        Index("ix_holding_security_as_of", "security_id", "as_of_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    as_of_date: Mapped[date_type] = mapped_column(Date, primary_key=True)

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    security_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("security.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Signed: negative is a short position. Rare, but a schema that cannot
    # express one silently corrupts the portfolio total of anyone who holds one.
    quantity: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)

    institution_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    institution_value_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Nullable and genuinely unknown for transferred-in positions: brokerages
    # frequently fail to carry basis across an ACATS. Defaulting it to zero
    # would report the entire position as gain, which is both wrong and the kind
    # of wrong that shows up on a tax return.
    cost_basis_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # institution_value_cents - round(quantity * institution_price_cents).
    # Normally 0; kept so a mismatch is auditable rather than invisible.
    value_drift_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    security: Mapped["Security"] = relationship(lazy="joined")  # noqa: F821
