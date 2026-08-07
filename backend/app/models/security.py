import uuid
from datetime import date as date_type

from sqlalchemy import BigInteger, Boolean, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class Security(Base, TimestampMixin):
    """A tradeable instrument, shared across every account that holds it.

    **Global, not per-user.** VTI held in a Roth IRA and VTI held in a taxable
    brokerage are the same security at the same price. Duplicating the row per
    account would let the two copies drift to different prices and make a
    portfolio total depend on which account you asked from.

    **Identity is `provider_security_id`, not the ticker.** Tickers are reused
    after delistings, differ across exchanges, and are absent entirely for many
    mutual funds and all cash positions. `ticker_symbol` is display metadata; the
    provider's opaque id is what the upsert keys on. Securities created outside
    a provider sync (manual entries, the demo seed) mint a synthetic id under a
    reserved prefix so the same column stays authoritative.

    **Two taxonomies on purpose.** `type` is what the instrument *is*; the asset
    class is what it is *exposed to*, and only the second one is meaningful in an
    allocation chart. See models/enums.AssetClass.
    """

    __tablename__ = "security"
    __table_args__ = (
        UniqueConstraint("provider_security_id", name="uq_security_provider_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_security_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    ticker_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    cusip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # EQUITY | ETF | MUTUAL_FUND | CRYPTO | FIXED_INCOME | CASH_EQUIVALENT
    type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    # US_EQUITY | INTERNATIONAL_EQUITY | FIXED_INCOME | CRYPTO | REAL_ESTATE | CASH
    asset_class: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    # Set when the user reclassifies by hand, so the next sync's inference does
    # not quietly undo them.
    asset_class_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # Last close the provider reported. Nullable because a brand-new security can
    # be known before it is priced, and because cash positions have no close.
    close_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    close_price_as_of: Mapped[date_type | None] = mapped_column(Date, nullable=True)
    # The close *before* the one above. Holding it here is what makes a 1-day
    # change computable without a price history table: the alternative is
    # diffing two holding snapshots, which conflates market movement with
    # trades that happened between them.
    previous_close_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    # A cash sweep or money-market position inside a brokerage. Excluded from
    # "invested" return figures — counting an idle cash balance as a holding
    # whose cost basis equals its value drags every performance number toward
    # zero for a reason that has nothing to do with performance.
    is_cash_equivalent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
