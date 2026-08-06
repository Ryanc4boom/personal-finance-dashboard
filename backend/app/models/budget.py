import uuid
from datetime import date as date_type

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Budget(Base, TimestampMixin):
    """A spending limit for one category, repeating every period.

    One row per (user, category) holds the *current* limit rather than one row
    per month. Rollover is therefore recomputed by replaying prior periods
    against the limit in force today, which means editing a limit retroactively
    changes historical rollover. That is the deliberate trade for not carrying a
    row per category per month; `effective_from` bounds how far back the replay
    walks so rollover cannot accumulate from before the budget existed.
    """

    __tablename__ = "budget"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_budget_user_category"),
        CheckConstraint("limit_cents >= 0", name="ck_budget_limit_non_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("category.id", ondelete="CASCADE"), nullable=False, index=True
    )

    limit_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # MONTHLY | WEEKLY | QUARTERLY | YEARLY
    period: Mapped[str] = mapped_column(String(16), nullable=False, server_default="MONTHLY")
    # First day of the first period this budget applies to.
    effective_from: Mapped[date_type] = mapped_column(Date, nullable=False)

    rollover_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
