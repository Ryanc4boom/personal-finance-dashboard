from pydantic import BaseModel, Field

from app.schemas import StrictRequest


class LinkTokenResponse(BaseModel):
    link_token: str
    expiration: str | None = None


class SetAccessTokenRequest(StrictRequest):
    public_token: str = Field(..., description="public_token returned by Plaid Link onSuccess")


class SetAccessTokenResponse(BaseModel):
    item_id: str
    institution_name: str | None = None
    accounts_linked: int
    sync: dict


class SyncRequest(StrictRequest):
    item_id: str | None = Field(
        None, description="Sync a single item. Omitted syncs every item for the user."
    )


class SyncResponse(BaseModel):
    results: list[dict]
