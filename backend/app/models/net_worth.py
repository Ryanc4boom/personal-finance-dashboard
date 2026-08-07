import uuid
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NetWorthSnapshot(Base):
    """Total assets minus total liabilities for one user on one day.

    **Derived data, stored anyway.** Everything here is recomputable from
    transactions and holdings, which normally argues against a table. It is
    materialised because the alternative is replaying two years of transaction
    history on every page load of a chart that spans two years — and because a
    snapshot is a record of what was true on a day, which is not the same claim
    as what today's data implies about that day. Regenerating is explicit
    (`POST /net-worth/backfill`), never a silent side effect of reading.

    **`net_worth_cents` is stored, not generated.** It is always
    `total_assets_cents - total_liabilities_cents`, and a CHECK constraint holds
    that. Storing it keeps the chart's hot path a single indexed column scan.

    **Liabilities are stored positive.** A $1,284.10 card balance is
    `total_liabilities_cents = 128410`, not `-128410`. The sign lives in the
    subtraction, once, in one place — the same discipline
    services/normalize.py applies to transaction amounts.

    `breakdown_json` holds the per-bucket detail behind the two totals so the
    stacked view needs no join back to accounts that may since have been closed,
    renamed or unlinked. It is a denormalised record of a moment, and rewriting
    history is the one thing it must not do.
    """

    __tablename__ = "net_worth_snapshot"
    __table_args__ = (
        UniqueConstraint("user_id", "snapshot_date", name="uq_net_worth_snapshot_user_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    snapshot_date: Mapped[date_type] = mapped_column(Date, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )

    total_assets_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Positive magnitude. See the class docstring.
    total_liabilities_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_worth_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Per-bucket detail: cash, investments, other assets, credit, loans.
    breakdown_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    # ACTUAL — every account had real data on this date.
    # RECONSTRUCTED — balances were walked backwards from today's figures.
    # Surfaced in the API so the chart can mark where the record stops being
    # observation and starts being inference.
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="RECONSTRUCTED"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
