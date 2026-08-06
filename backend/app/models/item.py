import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Item(Base, TimestampMixin):
    """A linked Plaid Item (one bank login), owning one or more accounts."""

    __tablename__ = "item"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    institution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("institution.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_item_id: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    # Fernet ciphertext — never stored or logged in plaintext.
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    # Plaid /transactions/sync cursor. NULL means "never synced" (full backfill).
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="good")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user: Mapped["User"] = relationship(back_populates="items")  # noqa: F821
    institution: Mapped["Institution | None"] = relationship(back_populates="items")  # noqa: F821
    accounts: Mapped[list["Account"]] = relationship(  # noqa: F821
        back_populates="item", cascade="all, delete-orphan"
    )
