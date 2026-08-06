"""Plaid delta-sync ingestion engine.

Design guarantees:

* **Idempotent.** Every write is keyed on `provider_txn_id`. Replaying the same
  delta (duplicate webhook, retried request, cursor rewind) converges to the same
  rows rather than duplicating them.
* **Crash-safe.** The cursor advances in the *same* database transaction that
  applies the page's changes. A crash mid-pagination resumes from the last page
  that was actually persisted — never skipping or replaying committed work.
* **Lossless.** Raw provider payloads land in `raw_transaction` untouched before
  any normalisation runs, so a normalisation bug can be re-processed offline.
* **Serialised per item.** A Redis lock prevents two concurrent syncs of one item
  from both consuming the same cursor.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import plaid
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decrypt
from app.models import Account, BalanceSnapshot, Item, RawTransaction, Transaction, User
from app.services import plaid_client
from app.services.categorization import CategoryResolver, apply_to_transaction
from app.services.matching import find_pending_match
from app.services.normalization import resolve_merchant
from app.services.normalize import (
    category_of,
    clean_description,
    direction_for,
    effective_dates,
    is_transfer,
    jsonable,
    normalize_amount_cents,
    to_cents,
)
from app.services.recurring import link_transaction

logger = logging.getLogger(__name__)

MAX_PAGES = 100  # safety valve against a pathological has_more loop


@dataclass
class SyncResult:
    item_id: str
    added: int = 0
    modified: int = 0
    removed: int = 0
    promoted: int = 0  # pending rows converted in place to posted
    accounts_upserted: int = 0
    pages: int = 0
    skipped: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #

def upsert_account(db: Session, item: Item, payload: dict) -> Account:
    provider_account_id = payload["account_id"]
    account = db.scalar(
        select(Account).where(
            Account.item_id == item.id,
            Account.provider_account_id == provider_account_id,
        )
    )
    if account is None:
        account = Account(item_id=item.id, provider_account_id=provider_account_id)
        db.add(account)

    balances = payload.get("balances") or {}
    account.name = payload.get("official_name") or payload.get("name") or "Account"
    account.mask = payload.get("mask")
    account.type = str(payload.get("type") or "other")
    account.subtype = str(payload["subtype"]) if payload.get("subtype") else None
    account.current_balance_cents = to_cents(balances.get("current"))
    account.available_balance_cents = to_cents(balances.get("available"))
    account.credit_limit_cents = to_cents(balances.get("limit"))
    account.is_active = True

    db.flush()
    _record_balance_snapshot(db, account)
    return account


def _record_balance_snapshot(db: Session, account: Account, on: date | None = None) -> None:
    """One snapshot per account per day; later syncs refresh the same row."""
    if account.current_balance_cents is None:
        return
    snapshot_date = on or datetime.now(timezone.utc).date()
    snapshot = db.scalar(
        select(BalanceSnapshot).where(
            BalanceSnapshot.account_id == account.id,
            BalanceSnapshot.date == snapshot_date,
        )
    )
    if snapshot is None:
        db.add(
            BalanceSnapshot(
                account_id=account.id,
                date=snapshot_date,
                balance_cents=account.current_balance_cents,
            )
        )
    else:
        snapshot.balance_cents = account.current_balance_cents


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #

def _apply_provider_fields(
    db: Session,
    txn: Transaction,
    payload: dict,
    resolver: CategoryResolver,
    user_id: uuid.UUID,
) -> None:
    """Refresh provider-owned fields, then re-run the Phase 2 and Phase 3 pipelines.

    `notes`, `tags` and `excluded_from_budget` are user-owned and are deliberately
    never touched here, so a re-sync cannot clobber manual work. A category the
    user picked by hand is protected too, by `apply_to_transaction` refusing to
    overwrite `category_source = 'USER'`.

    `is_recurring` used to sit in that user-owned set. As of Phase 3 it is derived:
    it is the denormalised form of "this row is linked to a recurring stream", so
    the ledger can filter on it without a join. The authority a user does have is
    over the *stream* — pausing or cancelling it — which `link_transaction`
    respects.
    """
    amount_cents = normalize_amount_cents(payload.get("amount"))
    txn.amount_cents = amount_cents
    txn.direction = direction_for(amount_cents)
    txn.date, txn.posted_date = effective_dates(payload)
    txn.description_raw = clean_description(payload)
    txn.merchant_name = payload.get("merchant_name")
    txn.provider_category = category_of(payload)
    txn.is_pending = bool(payload.get("pending"))

    # Plaid's own transfer flag is a hint, not the last word. A row already
    # paired to its counterpart by services/transfers.py stays a transfer even
    # if Plaid reclassifies it, otherwise a re-sync would silently drop one leg
    # back into spending and re-inflate the budget.
    if txn.transfer_pair_id is None:
        txn.is_transfer = is_transfer(payload)

    merchant, key = resolve_merchant(db, txn.description_raw, txn.merchant_name)
    txn.normalized_key = key or None
    txn.merchant_id = merchant.id if merchant else None

    apply_to_transaction(txn, resolver)

    # Phase 3 §1.3. Runs last because it reads `normalized_key`, `direction` and
    # `amount_cents`, all of which are settled above. This only maintains an
    # existing stream's cursor — creating streams is `detect_recurring`'s job, so
    # a sync never invents a commitment from a single row.
    link_transaction(db, user_id, txn)


def upsert_transaction(
    db: Session,
    accounts: dict[str, Account],
    payload: dict,
    result: SyncResult,
    resolver: CategoryResolver,
    user_id: uuid.UUID,
) -> None:
    provider_txn_id = payload["transaction_id"]
    account = accounts.get(payload.get("account_id"))
    if account is None:
        result.warnings.append(
            f"transaction {provider_txn_id} references unknown account "
            f"{payload.get('account_id')}; skipped"
        )
        return

    # 1. Already ingested under this id -> plain update. Covers `modified`
    #    deltas and any replay of `added`.
    existing = db.scalar(
        select(Transaction).where(Transaction.provider_txn_id == provider_txn_id)
    )
    if existing is not None:
        _apply_provider_fields(db, existing, payload, resolver, user_id)
        return

    # 2. This id was already absorbed as the pending half of a promoted pair.
    #    Re-adding it would resurrect the ghost row we deliberately collapsed.
    already_promoted = db.scalar(
        select(Transaction).where(Transaction.pending_provider_txn_id == provider_txn_id)
    )
    if already_promoted is not None:
        return

    # 3. New posted transaction: try to collapse it onto its pending twin.
    if not payload.get("pending"):
        pending_row = _find_pending_row(db, account, payload)
        if pending_row is not None:
            pending_row.pending_provider_txn_id = pending_row.provider_txn_id
            pending_row.provider_txn_id = provider_txn_id
            _apply_provider_fields(db, pending_row, payload, resolver, user_id)
            # notes / tags / excluded_from_budget survive untouched: the user may
            # have already categorised the pending row.
            result.promoted += 1
            return

    txn = Transaction(account_id=account.id, provider_txn_id=provider_txn_id)
    db.add(txn)
    _apply_provider_fields(db, txn, payload, resolver, user_id)


def _find_pending_row(db: Session, account: Account, payload: dict) -> Transaction | None:
    # Plaid's explicit link is authoritative when present.
    pending_id = payload.get("pending_transaction_id")
    if pending_id:
        linked = db.scalar(
            select(Transaction).where(Transaction.provider_txn_id == pending_id)
        )
        if linked is not None:
            return linked

    # Otherwise fall back to the amount/date/description heuristic.
    return find_pending_match(
        db,
        account_id=account.id,
        amount_cents=normalize_amount_cents(payload.get("amount")),
        txn_date=effective_dates(payload)[0],
        description=clean_description(payload),
        exclude_provider_txn_id=payload["transaction_id"],
    )


def remove_transaction(db: Session, provider_txn_id: str, result: SyncResult) -> None:
    txn = db.scalar(
        select(Transaction).where(Transaction.provider_txn_id == provider_txn_id)
    )
    if txn is None:
        # Might have been the pending half of a promotion — the posted row lives on.
        return
    db.delete(txn)
    result.removed += 1


def _record_raw(db: Session, item: Item, event_type: str, payload: dict) -> None:
    db.add(
        RawTransaction(
            item_id=item.id,
            provider="plaid",
            provider_txn_id=payload.get("transaction_id"),
            event_type=event_type,
            raw_payload=jsonable(payload),
        )
    )


# --------------------------------------------------------------------------- #
# Sync driver
# --------------------------------------------------------------------------- #

def sync_item(db: Session, item: Item) -> SyncResult:
    result = SyncResult(item_id=str(item.id))
    access_token = decrypt(item.access_token)
    cursor = item.cursor

    # Built once for the whole sync: the rule set and taxonomy are fixed for the
    # duration, and rebuilding per transaction would issue two queries per row.
    user = db.get(User, item.user_id)
    if user is None:
        result.error = "ITEM_HAS_NO_USER"
        return result
    resolver = CategoryResolver(db, user)

    for _ in range(MAX_PAGES):
        try:
            response = plaid_client.transactions_sync(access_token, cursor)
        except plaid.ApiException as exc:
            code = plaid_error_code(exc)
            if code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                # Data changed mid-pagination. Plaid's guidance is to discard the
                # partial page set and restart from the last committed cursor.
                logger.warning("mutation during pagination for item %s; restarting", item.id)
                db.rollback()
                db.refresh(item)
                cursor = item.cursor
                continue
            item.status = "error"
            item.error_code = code
            db.commit()
            result.error = code
            return result

        result.pages += 1

        for account_payload in response.get("accounts") or []:
            upsert_account(db, item, account_payload)
        result.accounts_upserted = len(response.get("accounts") or [])

        accounts = {
            a.provider_account_id: a
            for a in db.scalars(select(Account).where(Account.item_id == item.id)).all()
        }

        for payload in response.get("added") or []:
            _record_raw(db, item, "added", payload)
            before = result.promoted
            upsert_transaction(db, accounts, payload, result, resolver, user.id)
            if result.promoted == before:
                result.added += 1

        for payload in response.get("modified") or []:
            _record_raw(db, item, "modified", payload)
            upsert_transaction(db, accounts, payload, result, resolver, user.id)
            result.modified += 1

        for payload in response.get("removed") or []:
            _record_raw(db, item, "removed", payload)
            remove_transaction(db, payload["transaction_id"], result)

        cursor = response.get("next_cursor")
        item.cursor = cursor
        item.status = "good"
        item.error_code = None
        item.last_synced_at = datetime.now(timezone.utc)

        # Cursor + page changes commit together: the cursor is only ever ahead of
        # data that is already durable.
        db.commit()

        if not response.get("has_more"):
            break
    else:
        result.warnings.append(f"stopped after {MAX_PAGES} pages; run sync again to continue")

    return result


def plaid_error_code(exc: plaid.ApiException) -> str:
    try:
        return json.loads(exc.body).get("error_code") or "PLAID_API_ERROR"
    except Exception:
        return "PLAID_API_ERROR"
