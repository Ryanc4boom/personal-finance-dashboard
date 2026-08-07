import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Account, FinancialGoal, FinancialGoalAccount, Item, User
from app.schemas.goals import GoalCreate, GoalOut, GoalsReportOut, GoalUpdate
from app.services import goals as goals_service
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/goals", tags=["goals"])


def _owned_goal(db: Session, user: User, goal_id: uuid.UUID) -> FinancialGoal:
    goal = db.scalar(
        select(FinancialGoal).where(
            FinancialGoal.id == goal_id, FinancialGoal.user_id == user.id
        )
    )
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goal


def _validate_accounts(db: Session, user: User, account_ids: list[uuid.UUID]) -> None:
    """Reject links to accounts the user does not own.

    Checked here rather than left to the FK, because the FK only proves the
    account exists — it would happily let one user's goal read another's
    balance.
    """
    if not account_ids:
        return
    unique = set(account_ids)
    found = set(
        db.scalars(
            select(Account.id)
            .join(Item, Account.item_id == Item.id)
            .where(Item.user_id == user.id, Account.id.in_(unique))
        ).all()
    )
    missing = unique - found
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown account(s): {', '.join(str(m) for m in sorted(missing))}",
        )


def _set_links(db: Session, goal: FinancialGoal, account_ids: list[uuid.UUID]) -> None:
    """Replace the goal's links wholesale, preserving rows that survive.

    Deleting every row and re-inserting would churn primary keys on each save
    for no benefit; the diff keeps unchanged links stable.
    """
    desired = set(account_ids)
    existing = {link.account_id: link for link in goal.linked_accounts}

    for account_id, link in existing.items():
        if account_id not in desired:
            db.delete(link)
    for account_id in desired - existing.keys():
        db.add(FinancialGoalAccount(goal_id=goal.id, account_id=account_id))


@router.get("", response_model=GoalsReportOut)
def list_goals(
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    """All goals with live progress, pacing and projected completion."""
    user = get_current_user(db)
    report = goals_service.build_report(db, user, include_archived=include_archived)
    return GoalsReportOut.model_validate(report)


@router.post("", response_model=GoalOut, status_code=201)
def create_goal(payload: GoalCreate, db: Session = Depends(get_db)):
    user = get_current_user(db)
    _validate_accounts(db, user, payload.linked_account_ids)

    data = payload.model_dump(exclude={"linked_account_ids"})
    data["category"] = payload.category.value

    goal = FinancialGoal(user_id=user.id, **data)
    db.add(goal)
    db.flush()
    for account_id in dict.fromkeys(payload.linked_account_ids):
        db.add(FinancialGoalAccount(goal_id=goal.id, account_id=account_id))
    db.commit()
    db.refresh(goal)

    return GoalOut.model_validate(goals_service.build_progress(db, user, goal))


@router.get("/{goal_id}", response_model=GoalOut)
def get_goal(goal_id: uuid.UUID, db: Session = Depends(get_db)):
    user = get_current_user(db)
    goal = _owned_goal(db, user, goal_id)
    return GoalOut.model_validate(goals_service.build_progress(db, user, goal))


@router.patch("/{goal_id}", response_model=GoalOut)
def update_goal(goal_id: uuid.UUID, payload: GoalUpdate, db: Session = Depends(get_db)):
    """Partial update. Only fields present in the body are touched.

    `exclude_unset` is what makes `target_date: null` mean "clear the deadline"
    while omitting it means "leave it alone" — without it the two are
    indistinguishable and every save would silently wipe the date.
    """
    user = get_current_user(db)
    goal = _owned_goal(db, user, goal_id)

    fields = payload.model_dump(exclude_unset=True)
    account_ids = fields.pop("linked_account_ids", None)

    if account_ids is not None:
        _validate_accounts(db, user, account_ids)
        _set_links(db, goal, account_ids)

    if "category" in fields and fields["category"] is not None:
        fields["category"] = fields["category"].value

    for key, value in fields.items():
        setattr(goal, key, value)

    db.commit()
    db.refresh(goal)
    return GoalOut.model_validate(goals_service.build_progress(db, user, goal))


@router.delete("/{goal_id}", status_code=204)
def delete_goal(goal_id: uuid.UUID, db: Session = Depends(get_db)):
    """Hard delete. Use PATCH `is_archived: true` to keep the record."""
    user = get_current_user(db)
    goal = _owned_goal(db, user, goal_id)
    db.delete(goal)
    db.commit()
