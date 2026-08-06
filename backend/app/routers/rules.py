import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Category, CategorizationRule
from app.schemas.rules import (
    RecategorizeResult,
    RuleApplyResult,
    RuleCreate,
    RuleCreateResult,
    RuleOut,
)
from app.services.categorization import apply_rule_retroactively, recategorize_all
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/rules", tags=["rules"])


def _owned_category(db: Session, user, category_id: uuid.UUID) -> Category:
    category = db.scalar(
        select(Category).where(
            Category.id == category_id,
            or_(Category.user_id.is_(None), Category.user_id == user.id),
        )
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


def _to_out(rule: CategorizationRule, category: Category | None) -> RuleOut:
    out = RuleOut.model_validate(rule)
    if category is not None:
        out.target_category_name = category.name
        out.target_category_slug = category.slug
    return out


@router.get("", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)):
    user = get_current_user(db)
    rows = db.execute(
        select(CategorizationRule, Category)
        .join(Category, CategorizationRule.target_category_id == Category.id)
        .where(CategorizationRule.user_id == user.id)
        .order_by(
            CategorizationRule.priority.desc(), CategorizationRule.created_at.desc()
        )
    ).all()
    return [_to_out(rule, category) for rule, category in rows]


@router.post("", response_model=RuleCreateResult, status_code=201)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    """Create a rule, optionally applying it to history in the same call.

    Retroactive application is part of this request rather than a follow-up so
    the UI's one-click "create and apply" cannot half-succeed.
    """
    user = get_current_user(db)
    category = _owned_category(db, user, payload.target_category_id)

    rule = CategorizationRule(user_id=user.id, **payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)

    applied = None
    if rule.applies_retroactively:
        applied = RuleApplyResult(**apply_rule_retroactively(db, user, rule))

    return RuleCreateResult(rule=_to_out(rule, category), applied=applied)


@router.post("/{rule_id}/apply", response_model=RuleApplyResult)
def apply_rule(
    rule_id: uuid.UUID,
    force: bool = Query(
        False,
        description=(
            "Also overwrite categories the user set by hand. Off by default so a "
            "bulk apply cannot silently destroy manual work."
        ),
    ),
    db: Session = Depends(get_db),
):
    user = get_current_user(db)
    rule = db.scalar(
        select(CategorizationRule).where(
            CategorizationRule.id == rule_id, CategorizationRule.user_id == user.id
        )
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    return RuleApplyResult(**apply_rule_retroactively(db, user, rule, force=force))


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db)):
    """Delete a rule. Categories it already applied are left as they are.

    Reverting them would mean re-running the engine and guessing what the user
    wanted, so the destructive interpretation is avoided on purpose. Run
    /recategorize explicitly to re-derive.
    """
    user = get_current_user(db)
    rule = db.scalar(
        select(CategorizationRule).where(
            CategorizationRule.id == rule_id, CategorizationRule.user_id == user.id
        )
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    db.delete(rule)
    db.commit()


@router.post("/recategorize", response_model=RecategorizeResult)
def recategorize(
    only_uncategorized: bool = Query(
        False, description="Restrict the pass to transactions still in the holding pen"
    ),
    force: bool = Query(False, description="Also overwrite manual picks"),
    db: Session = Depends(get_db),
):
    """Re-run all four layers across the user's history."""
    user = get_current_user(db)
    return RecategorizeResult(
        **recategorize_all(
            db, user, force=force, only_uncategorized=only_uncategorized
        )
    )
