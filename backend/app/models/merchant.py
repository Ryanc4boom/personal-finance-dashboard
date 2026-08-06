import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Merchant(Base, TimestampMixin):
    """Canonical payee, keyed on the normalised form of the bank description.

    Deduplication is deliberately two-tier:

    * `normalized_key` is an exact key produced by services/normalization.py.
      Identical keys are the same merchant, no judgement required.
    * `canonical_merchant_id` records a *fuzzy* decision. When a new key is close
      enough to an existing merchant (trigram similarity), we still create its
      own row and point it at the canonical one rather than silently folding the
      two together. The alias stays visible and can be repointed if the guess was
      wrong — a fuzzy match that is merely absorbed is impossible to audit later.

    Resolution follows exactly one hop; aliases never chain.
    """

    __tablename__ = "merchant"

    id: Mapped[uuid.UUID] = uuid_pk()
    normalized_key: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    canonical_merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchant.id", ondelete="SET NULL"), nullable=True, index=True
    )
    default_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("category.id", ondelete="SET NULL"), nullable=True, index=True
    )

    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How the alias was decided, for auditing: EXACT | TRIGRAM | MANUAL
    link_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Trigram score at link time (0-1000 as integer permille — no floats stored).
    link_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    canonical: Mapped["Merchant | None"] = relationship(
        remote_side="Merchant.id", foreign_keys=[canonical_merchant_id]
    )
