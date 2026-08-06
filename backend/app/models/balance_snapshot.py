import uuid
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BalanceSnapshot(Base):
    """Daily point-in-time balance per account.

    Stored as a TimescaleDB hypertable partitioned on `date`. Timescale requires
    every unique index to include the partitioning column, hence the composite
    primary key (id, date).
    """

    __tablename__ = "balance_snapshot"
    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_balance_snapshot_account_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    date: Mapped[date_type] = mapped_column(Date, primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    balance_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
