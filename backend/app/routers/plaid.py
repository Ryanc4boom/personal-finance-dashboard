import json
import logging
import uuid

import plaid
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.redis_client import sync_lock
from app.core.security import encrypt
from app.models import Institution, Item
from app.schemas.plaid import (
    LinkTokenResponse,
    SetAccessTokenRequest,
    SetAccessTokenResponse,
    SyncRequest,
    SyncResponse,
)
from app.services import ingestion, plaid_client, plaid_webhook
from app.services.users import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/plaid", tags=["plaid"])


@router.post("/link-token", response_model=LinkTokenResponse)
def create_link_token(db: Session = Depends(get_db)):
    user = get_current_user(db)
    try:
        response = plaid_client.create_link_token(str(user.id))
    except plaid.ApiException as exc:
        raise HTTPException(status_code=502, detail=_plaid_detail(exc)) from exc
    return LinkTokenResponse(
        link_token=response["link_token"], expiration=response.get("expiration")
    )


@router.post("/set-access-token", response_model=SetAccessTokenResponse)
def set_access_token(payload: SetAccessTokenRequest, db: Session = Depends(get_db)):
    """Exchange a Link public_token for an access_token and backfill the item."""
    user = get_current_user(db)

    try:
        exchange = plaid_client.exchange_public_token(payload.public_token)
        access_token = exchange["access_token"]
        provider_item_id = exchange["item_id"]
        item_info = plaid_client.get_item(access_token)
    except plaid.ApiException as exc:
        raise HTTPException(status_code=502, detail=_plaid_detail(exc)) from exc

    institution = _upsert_institution(db, (item_info.get("item") or {}).get("institution_id"))

    # Re-linking an existing item must not create a duplicate: reuse the row and
    # refresh its token, keeping the cursor so we resume rather than re-backfill.
    #
    # Scoped to the caller. Matching on provider_item_id alone would overwrite
    # the access_token of a row belonging to someone else while leaving its
    # user_id untouched — the token would be filed under an account that did not
    # obtain it. Today there is one user so this cannot fire, but the fix is
    # free now and this is exactly the query that silently becomes an
    # authorization bug the moment a second user exists.
    item = db.scalar(
        select(Item).where(
            Item.provider_item_id == provider_item_id,
            Item.user_id == user.id,
        )
    )
    if item is None:
        item = Item(user_id=user.id, provider_item_id=provider_item_id)
        db.add(item)
    item.access_token = encrypt(access_token)
    item.institution_id = institution.id if institution else None
    item.status = "good"
    item.error_code = None
    db.commit()
    db.refresh(item)

    result = _sync_one(db, item)
    return SetAccessTokenResponse(
        item_id=str(item.id),
        institution_name=institution.name if institution else None,
        accounts_linked=result.accounts_upserted,
        sync=result.as_dict(),
    )


@router.post("/sync", response_model=SyncResponse)
def sync(payload: SyncRequest | None = None, db: Session = Depends(get_db)):
    """Pull the transaction delta for one item, or all of the user's items."""
    user = get_current_user(db)

    stmt = select(Item).where(Item.user_id == user.id)
    if payload and payload.item_id:
        try:
            stmt = stmt.where(Item.id == uuid.UUID(payload.item_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="item_id must be a UUID") from None

    items = db.scalars(stmt).all()
    if not items:
        raise HTTPException(status_code=404, detail="No linked items to sync")

    return SyncResponse(results=[_sync_one(db, item).as_dict() for item in items])


@router.post("/webhook", status_code=202)
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Plaid webhook receiver.

    Returns 2xx for anything that is genuinely from Plaid: Plaid retries on
    non-2xx, and a retry storm over an unrecognised webhook type is worse than
    dropping it. Real failures are surfaced through item.status/error_code
    instead.

    A failed signature check is the one exception and answers 401. Retries do
    not matter there — a forged request has no one to retry it, and a genuine
    webhook that fails verification (a clock far enough out of sync to blow the
    freshness window) *should* be retried once the cause is fixed.
    """
    # Raw bytes, before any parsing: the signature covers the exact payload
    # Plaid transmitted, and `await request.json()` would discard it.
    raw_body = await request.body()
    try:
        plaid_webhook.verify(request.headers.get(plaid_webhook.VERIFICATION_HEADER), raw_body)
    except plaid_webhook.WebhookVerificationError as exc:
        logger.warning("Rejected unverified Plaid webhook: %s", exc)
        raise HTTPException(status_code=401, detail="Webhook verification failed") from exc

    try:
        body = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Body is not valid JSON") from exc

    webhook_type = body.get("webhook_type")
    webhook_code = body.get("webhook_code")
    provider_item_id = body.get("item_id")

    item = db.scalar(select(Item).where(Item.provider_item_id == provider_item_id))
    if item is None:
        logger.warning("webhook for unknown item %s", provider_item_id)
        return {"status": "ignored", "reason": "unknown item"}

    if webhook_type == "TRANSACTIONS" and webhook_code in {
        "SYNC_UPDATES_AVAILABLE",
        "INITIAL_UPDATE",
        "HISTORICAL_UPDATE",
        "DEFAULT_UPDATE",
        "TRANSACTIONS_REMOVED",
    }:
        result = _sync_one(db, item)
        return {"status": "synced", "result": result.as_dict()}

    if webhook_type == "ITEM" and webhook_code == "ERROR":
        item.status = "error"
        item.error_code = (body.get("error") or {}).get("error_code")
        db.commit()
        return {"status": "item marked errored"}

    if webhook_type == "ITEM" and webhook_code == "PENDING_EXPIRATION":
        item.status = "pending_expiration"
        db.commit()
        return {"status": "item marked pending_expiration"}

    return {"status": "ignored", "reason": f"{webhook_type}/{webhook_code}"}


# --------------------------------------------------------------------------- #

def _sync_one(db: Session, item: Item) -> ingestion.SyncResult:
    with sync_lock(str(item.id)) as acquired:
        if not acquired:
            return ingestion.SyncResult(
                item_id=str(item.id),
                skipped=True,
                warnings=["another sync is already running for this item"],
            )
        return ingestion.sync_item(db, item)


def _upsert_institution(db: Session, provider_institution_id: str | None) -> Institution | None:
    if not provider_institution_id:
        return None

    institution = db.scalar(
        select(Institution).where(
            Institution.provider_institution_id == provider_institution_id
        )
    )
    if institution is not None:
        return institution

    details = (plaid_client.get_institution(provider_institution_id) or {}).get("institution") or {}
    logo = details.get("logo")
    institution = Institution(
        provider_institution_id=provider_institution_id,
        name=details.get("name") or provider_institution_id,
        logo_url=f"data:image/png;base64,{logo}" if logo else None,
    )
    db.add(institution)
    db.commit()
    db.refresh(institution)
    return institution


def _plaid_detail(exc: plaid.ApiException) -> str:
    try:
        body = json.loads(exc.body)
        return body.get("error_message") or body.get("error_code") or "Plaid API error"
    except Exception:
        return "Plaid API error"
