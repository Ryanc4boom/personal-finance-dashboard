import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Account(Base, TimestampMixin):
    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("item_id", "provider_account_id", name="uq_account_item_provider"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_account_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mask: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # depository | credit | loan | investment | other
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # All money is integer minor units (cents). Never float.
    current_balance_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    available_balance_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credit_limit_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    item: Mapped["Item"] = relationship(back_populates="accounts")  # noqa: F821
    transactions: Mapped[list["Transaction"]] = relationship(  # noqa: F821
        back_populates="account", cascade="all, delete-orphan"
    )
