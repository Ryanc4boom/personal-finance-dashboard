import uuid

from pydantic import BaseModel, ConfigDict


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    kind: str
    parent_id: uuid.UUID | None
    icon: str | None
    color: str | None
    sort_order: int
    is_system: bool


class CategoryNode(CategoryOut):
    """A parent with its children inlined, for the picker UI."""

    children: list[CategoryOut] = []


class CategoryTree(BaseModel):
    parents: list[CategoryNode]
    total: int
