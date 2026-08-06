import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class CategorizationRule(Base, TimestampMixin):
    """Layer 1 of the categorisation engine — an explicit user instruction.

    Priority convention: **higher number wins**. Rules are evaluated in
    descending priority and the first match stops evaluation; ties break on
    newest-created-first so a freshly saved rule beats an older one of equal
    priority. This is stated here because "priority" is read both ways in the
    wild and the whole engine hinges on it.

    Two families of match live in one table, so the columns they use differ:
    EXACT_MERCHANT / DESCRIPTION_CONTAINS / ACCOUNT_ID read `match_value`, while
    AMOUNT_EQUALS / AMOUNT_RANGE read the cent columns. The CHECK constraint
    below makes an under-specified rule unstorable rather than silently
    never-matching, which is far harder to debug.
    """

    __tablename__ = "categorization_rule"
    __table_args__ = (
        CheckConstraint(
            "(match_type IN ('EXACT_MERCHANT', 'DESCRIPTION_CONTAINS', 'ACCOUNT_ID') "
            "  AND match_value IS NOT NULL AND length(trim(match_value)) > 0) "
            "OR (match_type = 'AMOUNT_EQUALS' AND amount_min_cents IS NOT NULL) "
            "OR (match_type = 'AMOUNT_RANGE' AND amount_min_cents IS NOT NULL "
            "  AND amount_max_cents IS NOT NULL AND amount_max_cents >= amount_min_cents)",
            name="ck_rule_match_fields_present",
        ),
        Index("ix_rule_user_priority", "user_id", "priority"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # EXACT_MERCHANT | DESCRIPTION_CONTAINS | AMOUNT_EQUALS | AMOUNT_RANGE | ACCOUNT_ID
    match_type: Mapped[str] = mapped_column(String(32), nullable=False)
    match_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Amounts are compared on the absolute value in cents, so a user does not
    # have to reason about our inflow/outflow sign convention when writing a rule.
    amount_min_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    amount_max_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    target_category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("category.id", ondelete="CASCADE"), nullable=False, index=True
    )

    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    applies_retroactively: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
