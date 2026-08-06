"""Thin wrapper over the Plaid Python SDK.

Keeps SDK model construction out of routers/services so the rest of the codebase
deals in plain dicts.
"""

from functools import lru_cache

import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.institutions_get_by_id_request_options import (
    InstitutionsGetByIdRequestOptions,
)
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_sync_request_options import TransactionsSyncRequestOptions

from app.core.config import settings
from app.services.normalize import jsonable

_ENVIRONMENTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


@lru_cache
def get_client() -> plaid_api.PlaidApi:
    if not settings.plaid_client_id or not settings.plaid_secret:
        raise RuntimeError("PLAID_CLIENT_ID and PLAID_SECRET must be set in .env")

    host = _ENVIRONMENTS.get(settings.plaid_env.lower())
    if host is None:
        raise RuntimeError(
            f"Unsupported PLAID_ENV={settings.plaid_env!r}. Use 'sandbox' or 'production'."
        )

    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": settings.plaid_client_id,
            "secret": settings.plaid_secret,
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def create_link_token(user_id: str) -> dict:
    request = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=str(user_id)),
        client_name="Budgeting Platform",
        products=[Products(p) for p in settings.plaid_product_list],
        country_codes=[CountryCode(c) for c in settings.plaid_country_code_list],
        language="en",
        **({"webhook": settings.plaid_webhook_url} if settings.plaid_webhook_url else {}),
    )
    return jsonable(get_client().link_token_create(request).to_dict())


def exchange_public_token(public_token: str) -> dict:
    request = ItemPublicTokenExchangeRequest(public_token=public_token)
    return jsonable(get_client().item_public_token_exchange(request).to_dict())


def get_item(access_token: str) -> dict:
    return jsonable(get_client().item_get(ItemGetRequest(access_token=access_token)).to_dict())


def get_institution(institution_id: str) -> dict | None:
    try:
        request = InstitutionsGetByIdRequest(
            institution_id=institution_id,
            country_codes=[CountryCode(c) for c in settings.plaid_country_code_list],
            options=InstitutionsGetByIdRequestOptions(include_optional_metadata=True),
        )
        return jsonable(get_client().institutions_get_by_id(request).to_dict())
    except plaid.ApiException:
        # Institution metadata is cosmetic; never fail a link over it.
        return None


def transactions_sync(access_token: str, cursor: str | None, count: int = 500) -> dict:
    options = TransactionsSyncRequestOptions(include_personal_finance_category=True)
    kwargs = {"access_token": access_token, "count": count, "options": options}
    if cursor:
        kwargs["cursor"] = cursor
    return jsonable(get_client().transactions_sync(TransactionsSyncRequest(**kwargs)).to_dict())
