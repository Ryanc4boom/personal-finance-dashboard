from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Account, Institution, Item
from app.schemas.ledger import AccountOut, AccountSummary
from app.services.users import get_current_user

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])

CASH_TYPES = {"depository"}
LIABILITY_TYPES = {"credit"}


@router.get("", response_model=AccountSummary)
def account_summary(db: Session = Depends(get_db)):
    """Balances split into cash vs. credit liabilities, all in integer cents."""
    user = get_current_user(db)

    rows = db.execute(
        select(Account, Institution.name)
        .join(Item, Account.item_id == Item.id)
        .outerjoin(Institution, Item.institution_id == Institution.id)
        .where(Item.user_id == user.id, Account.is_active.is_(True))
        .order_by(Account.type, Account.name)
    ).all()

    accounts: list[AccountOut] = []
    cash = 0
    liabilities = 0

    for account, institution_name in rows:
        out = AccountOut.model_validate(account)
        out.institution_name = institution_name
        accounts.append(out)

        balance = account.current_balance_cents or 0
        if account.type in CASH_TYPES:
            cash += balance
        elif account.type in LIABILITY_TYPES:
            # Plaid reports credit balances as a positive amount owed. Keep the
            # stored value provider-faithful and present it as a liability here.
            liabilities += balance

    return AccountSummary(
        depository_cash_cents=cash,
        credit_liabilities_cents=liabilities,
        net_cents=cash - liabilities,
        accounts=accounts,
    )
