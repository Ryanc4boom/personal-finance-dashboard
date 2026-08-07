import uuid
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class InvestmentTransaction(Base, TimestampMixin):
    """A trade, distribution or cash movement inside an investment account.

    **Kept apart from `transaction` deliberately.** They look similar and are
    not: a ledger transaction is spendable cash moving in or out of a bank
    account and belongs in budgets, categorisation and the cash flow forecast. A
    BUY is none of those — it rearranges value inside the account without
    changing its total, and folding it into the ledger would report every
    401(k) contribution as a $500 expense and every rebalance as thousands of
    dollars of spending. Phase 2's budgets and Phase 3's forecast read
    `transaction`, so this separation is what keeps them correct.

    **Sign convention matches the ledger.** `amount_cents` is positive when cash
    enters the account (SELL, DIVIDEND, INTEREST, an inbound TRANSFER) and
    negative when it leaves (BUY, FEE, an outbound TRANSFER). Same rule as
    `transaction.amount_cents`, so the two can be reasoned about together
    without a lookup table of exceptions.

    `security_id` is nullable: a cash sweep, an account fee and a wire have no
    instrument attached.
    """

    __tablename__ = "investment_transaction"
    __table_args__ = (
        UniqueConstraint(
            "provider_investment_txn_id", name="uq_investment_transaction_provider_id"
        ),
        Index("ix_investment_transaction_account_date", "account_id", "date"),
        Index("ix_investment_transaction_security_date", "security_id", "date"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("security.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_investment_txn_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )

    # BUY | SELL | DIVIDEND | INTEREST | FEE | TRANSFER
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # Provider's own finer-grained label, kept verbatim for auditing.
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Signed, positive = cash into the account. See the class docstring.
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Signed: positive on a BUY, negative on a SELL. Null for cash-only rows.
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 8), nullable=True)
    price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fees_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")

    security: Mapped["Security | None"] = relationship(lazy="joined")  # noqa: F821
