import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Budget, Category
from app.models.enums import BudgetPeriod
from app.schemas.budgets import (
    ApplySuggestionsResult,
    BudgetLineOut,
    BudgetOut,
    BudgetReportOut,
    BudgetSuggestionOut,
    BudgetUpsert,
    SuggestionsOut,
)
from app.services import budgets as budget_service
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/budgets", tags=["budgets"])


def _parse_period_anchor(value: str | None) -> date:
    """Accept 'YYYY-MM' (what the period selector sends) or a full date."""
    if not value:
        return date.today()
    try:
        if len(value) == 7:
            year, month = value.split("-")
            return date(int(year), int(month), 1)
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="period must be 'YYYY-MM' or 'YYYY-MM-DD'"
        )


@router.get("", response_model=BudgetReportOut)
def get_budgets(
    period: str | None = Query(None, description="Month to report on, e.g. '2026-07'"),
    period_type: str = Query(BudgetPeriod.MONTHLY.value),
    db: Session = Depends(get_db),
):
    """Budget lines for one period, including spend, rollover and pacing."""
    user = get_current_user(db)
    anchor = _parse_period_anchor(period)

    report = budget_service.build_report(db, user, anchor=anchor, period=period_type)
    return BudgetReportOut(
        period_start=report.period_start,
        period_end=report.period_end,
        period=report.period,
        elapsed_days=report.elapsed_days,
        total_days=report.total_days,
        total_limit_cents=report.total_limit_cents,
        total_spent_cents=report.total_spent_cents,
        total_projected_cents=report.total_projected_cents,
        lines=[BudgetLineOut.model_validate(line) for line in report.lines],
    )


@router.put("", response_model=BudgetOut)
def upsert_budget(payload: BudgetUpsert, db: Session = Depends(get_db)):
    """Set or update the limit for one category."""
    user = get_current_user(db)

    category = db.scalar(
        select(Category).where(
            Category.id == payload.category_id,
            or_(Category.user_id.is_(None), Category.user_id == user.id),
        )
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    budget = db.scalar(
        select(Budget).where(
            Budget.user_id == user.id, Budget.category_id == payload.category_id
        )
    )
    if budget is None:
        budget = Budget(
            user_id=user.id,
            category_id=payload.category_id,
            effective_from=budget_service.month_start(date.today()),
        )
        db.add(budget)

    budget.limit_cents = payload.limit_cents
    budget.period = payload.period
    budget.rollover_enabled = payload.rollover_enabled
    budget.is_active = payload.is_active

    db.commit()
    db.refresh(budget)
    return BudgetOut.model_validate(budget)


@router.delete("/{category_id}", status_code=204)
def delete_budget(category_id: uuid.UUID, db: Session = Depends(get_db)):
    user = get_current_user(db)
    budget = db.scalar(
        select(Budget).where(Budget.user_id == user.id, Budget.category_id == category_id)
    )
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    db.delete(budget)
    db.commit()


@router.get("/suggestions", response_model=SuggestionsOut)
def get_suggestions(db: Session = Depends(get_db)):
    """Trailing-median monthly spend per parent category."""
    user = get_current_user(db)
    suggestions = budget_service.suggest_limits(db, user)
    return SuggestionsOut(
        months_analyzed=budget_service.SUGGESTION_MONTHS,
        suggestions=[BudgetSuggestionOut.model_validate(s) for s in suggestions],
    )


@router.post("/suggestions/apply", response_model=ApplySuggestionsResult)
def apply_budget_suggestions(
    overwrite: bool = Query(
        False, description="Replace limits the user has already tuned"
    ),
    db: Session = Depends(get_db),
):
    user = get_current_user(db)
    return ApplySuggestionsResult(
        **budget_service.apply_suggestions(db, user, overwrite=overwrite)
    )
