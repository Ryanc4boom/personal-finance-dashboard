import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Account, Category, Item, Transaction
from app.models.enums import CategorySource
from app.schemas.ledger import TransactionOut, TransactionPage, TransactionUpdate
from app.services.categorization import set_category_manually
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


def _to_out(txn: Transaction, account_name: str, account_mask: str | None) -> TransactionOut:
    """Flatten the eager-loaded category/merchant rows onto the response."""
    out = TransactionOut.model_validate(txn)
    out.account_name = account_name
    out.account_mask = account_mask
    if txn.category is not None:
        out.category_name = txn.category.name
        out.category_slug = txn.category.slug
    if txn.merchant is not None:
        out.merchant_display_name = txn.merchant.display_name
    return out


@router.get("", response_model=TransactionPage)
def list_transactions(
    db: Session = Depends(get_db),
    search: str | None = Query(None, description="Case-insensitive description/merchant match"),
    account_id: uuid.UUID | None = Query(None),
    account_type: str | None = Query(
        None, description="Filter to an account class, e.g. 'depository' or 'credit'"
    ),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    is_pending: bool | None = Query(None),
    category_id: uuid.UUID | None = Query(
        None, description="A parent category also matches its children"
    ),
    uncategorized: bool | None = Query(
        None, description="Only rows still in the holding pen"
    ),
    is_transfer: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    user = get_current_user(db)

    base = (
        select(Transaction, Account.name, Account.mask)
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(Item.user_id == user.id)
    )

    if account_id is not None:
        base = base.where(Transaction.account_id == account_id)
    if account_type is not None:
        base = base.where(Account.type == account_type)
    if start_date is not None:
        base = base.where(Transaction.date >= start_date)
    if end_date is not None:
        base = base.where(Transaction.date <= end_date)
    if is_pending is not None:
        base = base.where(Transaction.is_pending.is_(is_pending))
    if is_transfer is not None:
        base = base.where(Transaction.is_transfer.is_(is_transfer))
    if category_id is not None:
        # Selecting "Food & Drink" on the budget dashboard should show the
        # groceries and restaurant rows underneath it, not an empty table.
        child_ids = select(Category.id).where(Category.parent_id == category_id)
        base = base.where(
            or_(
                Transaction.category_id == category_id,
                Transaction.category_id.in_(child_ids),
            )
        )
    if uncategorized:
        base = base.where(
            or_(
                Transaction.category_id.is_(None),
                Transaction.category_source == CategorySource.UNCATEGORIZED.value,
            )
        )
    if search:
        pattern = f"%{search.strip()}%"
        base = base.where(
            or_(
                Transaction.description_raw.ilike(pattern),
                Transaction.merchant_name.ilike(pattern),
            )
        )

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    rows = db.execute(
        base.order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = [_to_out(txn, name, mask) for txn, name, mask in rows]
    return TransactionPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: uuid.UUID, db: Session = Depends(get_db)):
    user = get_current_user(db)
    row = db.execute(
        select(Transaction, Account.name, Account.mask)
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(Item.user_id == user.id, Transaction.id == transaction_id)
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return _to_out(*row)


@router.patch("/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: uuid.UUID, payload: TransactionUpdate, db: Session = Depends(get_db)
):
    """Edit the user-owned fields of one transaction.

    A category set here is recorded as `USER`, which makes it permanently
    immune to rules, merchant defaults and re-sync. That is the point of the
    inline dropdown: correcting a row once should stick forever.
    """
    user = get_current_user(db)
    row = db.execute(
        select(Transaction, Account.name, Account.mask)
        .join(Account, Transaction.account_id == Account.id)
        .join(Item, Account.item_id == Item.id)
        .where(Item.user_id == user.id, Transaction.id == transaction_id)
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn, account_name, account_mask = row
    fields = payload.model_dump(exclude_unset=True)

    if "category_id" in fields:
        category_id = fields.pop("category_id")
        if category_id is None:
            raise HTTPException(
                status_code=422,
                detail="category_id cannot be cleared; pick Uncategorized instead",
            )
        category = db.scalar(
            select(Category).where(
                Category.id == category_id,
                or_(Category.user_id.is_(None), Category.user_id == user.id),
            )
        )
        if category is None:
            raise HTTPException(status_code=404, detail="Category not found")
        set_category_manually(txn, category.id)

    for field, value in fields.items():
        setattr(txn, field, value)

    db.commit()
    db.refresh(txn)
    return _to_out(txn, account_name, account_mask)
