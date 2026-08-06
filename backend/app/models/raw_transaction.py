import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, uuid_pk


class RawTransaction(Base):
    """Untouched aggregator payloads, append-only.

    This is the system of record for re-processing. If a normalisation bug ships,
    we replay from here rather than re-pulling from Plaid (which would have moved
    its cursor on). Never mutate rows in this table.
    """

    __tablename__ = "raw_transaction"
    __table_args__ = (
        Index("ix_raw_transaction_item_received", "item_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default="plaid")
    provider_txn_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    # added | modified | removed
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
