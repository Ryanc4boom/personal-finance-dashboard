from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Category
from app.schemas.categories import CategoryNode, CategoryOut, CategoryTree
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])


@router.get("", response_model=CategoryTree)
def list_categories(db: Session = Depends(get_db)):
    """The full taxonomy as a parent/child tree, ordered for display."""
    user = get_current_user(db)

    categories = db.scalars(
        select(Category)
        .where(or_(Category.user_id.is_(None), Category.user_id == user.id))
        .order_by(Category.sort_order, Category.name)
    ).all()

    children: dict = {}
    for category in categories:
        if category.parent_id is not None:
            children.setdefault(category.parent_id, []).append(CategoryOut.model_validate(category))

    parents = [
        CategoryNode(**CategoryOut.model_validate(c).model_dump(), children=children.get(c.id, []))
        for c in categories
        if c.parent_id is None
    ]

    return CategoryTree(parents=parents, total=len(categories))
