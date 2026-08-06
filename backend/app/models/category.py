import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk


class Category(Base, TimestampMixin):
    """Two-level taxonomy node.

    Depth is capped at two by convention: a node with `parent_id IS NULL` is a
    parent, anything else is a child, and children never have children. Budgets
    may sit on either level — spend for a parent rolls its children up.

    `user_id IS NULL` marks a system category from the seeded taxonomy. A user
    may add their own categories alongside them, hence the two partial unique
    indexes: Postgres treats NULLs as distinct, so a plain UNIQUE(user_id, slug)
    would happily allow two system categories with the same slug.
    """

    __tablename__ = "category"
    __table_args__ = (
        Index(
            "uq_category_system_slug",
            "slug",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_category_user_slug",
            "user_id",
            "slug",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index("ix_category_parent_sort", "parent_id", "sort_order"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True, index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("category.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Hierarchical and stable: "food_drink", "food_drink.groceries". Code and
    # the provider mapping table reference categories by slug, never by name.
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    # INCOME | EXPENSE | TRANSFER
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    icon: Mapped[str | None] = mapped_column(String(48), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    parent: Mapped["Category | None"] = relationship(
        back_populates="children", remote_side="Category.id"
    )
    children: Mapped[list["Category"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
