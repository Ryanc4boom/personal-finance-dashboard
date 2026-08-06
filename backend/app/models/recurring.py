import uuid
from datetime import date as date_type

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class RecurringStream(Base, TimestampMixin):
    """A repeating charge or deposit, inferred from transaction history.

    **Identity.** A stream is keyed on `(user, normalized_key, frequency,
    direction)`, not on the merchant row. Three reasons:

    * `normalized_key` is NOT NULL, so a plain UNIQUE actually constrains.
      `merchant_id` is nullable, and in Postgres NULLs are distinct — a unique
      index over a nullable column silently permits duplicates.
    * `direction` separates a merchant's charges from its refunds. Blending them
      would produce a stream whose "expected amount" is the average of a $40
      purchase and a $40 return, which is zero.
    * `frequency` lets one merchant carry two genuine cadences (a monthly plan
      plus an annual add-on) rather than forcing them to overwrite each other.

    **Two amounts, on purpose.** `expected_amount_cents` is the *median* of the
    stream's observations and is the stable baseline; `last_amount_cents` is the
    most recent charge. Price-hike detection is exactly the comparison between
    them, which is only possible because the baseline is not overwritten every
    time a charge lands.
    """

    __tablename__ = "recurring_stream"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "normalized_key",
            "frequency",
            "direction",
            name="uq_recurring_stream_identity",
        ),
        CheckConstraint(
            "expected_amount_cents > 0", name="ck_recurring_stream_amount_positive"
        ),
        CheckConstraint("occurrence_count >= 2", name="ck_recurring_stream_occurrences"),
        # The forecaster's hot path: every active stream due inside a horizon.
        Index("ix_recurring_stream_user_status_next", "user_id", "status", "next_expected_date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchant.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("category.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Grouping identity, copied from `transaction.normalized_key`.
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Human-facing label, cached so the list view needs no merchant join.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # WEEKLY | BIWEEKLY | MONTHLY | QUARTERLY | ANNUALLY
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    # INFLOW | OUTFLOW — income streams and bill streams live in one table but
    # are never mixed, and the forecaster adds one and subtracts the other.
    direction: Mapped[str] = mapped_column(String(8), nullable=False, index=True)

    # Always positive magnitude; `direction` carries the sign.
    expected_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Median absolute deviation from the baseline, in basis points of the
    # baseline. 0 = every charge identical (a subscription); a few hundred = a
    # variable bill like electricity. Integer bps so no float ever touches money.
    amount_variance_bps: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    first_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    last_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    next_expected_date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    # Observed median gap. Kept alongside `frequency` because it is the evidence
    # for the classification: a stream tagged MONTHLY with a median of 34 days is
    # visibly a weaker fit than one at 30, without re-deriving anything.
    median_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # ACTIVE | PAUSED | CANCELLED
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    # AUTO | USER — a USER status is never overwritten by detection.
    status_source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="AUTO")

    is_subscription: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Set once the user edits `is_subscription` by hand, so the classifier stops
    # second-guessing them on the next run.
    is_subscription_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    merchant: Mapped["Merchant | None"] = relationship(lazy="joined")  # noqa: F821
    category: Mapped["Category | None"] = relationship(lazy="joined")  # noqa: F821
