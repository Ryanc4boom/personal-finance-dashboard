import uuid

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Institution(Base, TimestampMixin):
    __tablename__ = "institution"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_institution_id: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list["Item"]] = relationship(back_populates="institution")  # noqa: F821
