import uuid
from datetime import date as date_type

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class FinancialGoal(Base, TimestampMixin):
    """A savings target, optionally backed by real accounts.

    **`current_amount_cents` is a manual baseline, not the balance.** The spec
    lists it as a stored field, and it is one — but only for goals with no
    linked accounts, where the user is tracking something the platform cannot
    see. The moment a goal is linked to accounts, progress is computed live from
    those balances by services/goals.py and this column is ignored. Storing a
    copy of a linked balance would be stale the instant the next transaction
    lands, and a goal that silently reports last week's progress is worse than
    one that reports none.

    **Linked accounts live in a join table**, not a UUID array. An array cannot
    carry a foreign key, so deleting an account would leave a dangling id that
    every subsequent progress calculation would either skip silently or crash
    on. The API still presents `linked_account_ids` as a flat array — the
    contract is unchanged, only the storage is honest about the reference.

    `target_date` is nullable: "save $20k for a house" is a real goal without a
    deadline, and forcing a fake one would manufacture an off-track warning out
    of nothing.
    """

    __tablename__ = "financial_goal"
    __table_args__ = (
        CheckConstraint("target_amount_cents > 0", name="ck_financial_goal_target_positive"),
        CheckConstraint(
            "current_amount_cents >= 0", name="ck_financial_goal_current_non_negative"
        ),
        Index("ix_financial_goal_user_archived", "user_id", "is_archived"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # EMERGENCY_FUND | HOUSE_DOWN_PAYMENT | FIRE | CAR_PURCHASE | CUSTOM
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    target_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Manual baseline for unlinked goals only — see the class docstring.
    current_amount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    target_date: Mapped[date_type | None] = mapped_column(Date, nullable=True, index=True)

    # Contributions the user has committed to but which are not yet visible in
    # any balance. Feeds the projected completion date when there is not enough
    # history to infer a rate.
    monthly_contribution_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Archived rather than deleted: a hit goal is a record worth keeping.
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    linked_accounts: Mapped[list["FinancialGoalAccount"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan", lazy="selectin"
    )


class FinancialGoalAccount(Base):
    """Join row backing `FinancialGoal.linked_account_ids`.

    Exists so the reference is a real foreign key. `ondelete="CASCADE"` on the
    account side means unlinking a bank quietly narrows the goal's backing
    rather than leaving it pointing at a row that is gone.
    """

    __tablename__ = "financial_goal_account"
    __table_args__ = (
        UniqueConstraint("goal_id", "account_id", name="uq_financial_goal_account"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("financial_goal.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True
    )

    goal: Mapped["FinancialGoal"] = relationship(back_populates="linked_accounts")
