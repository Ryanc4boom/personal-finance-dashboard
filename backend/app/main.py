import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.auth import ApiKeyMiddleware
from app.core.config import settings
from app.routers import (
    accounts,
    budgets,
    categories,
    forecast,
    goals,
    investments,
    net_worth,
    plaid,
    recurring,
    research,
    rules,
    transactions,
    transfers,
)

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Budgeting Platform API",
    version="0.1.0",
    description=(
        "Phase 5 — automated stock research against SEC EDGAR filings and XBRL "
        "company facts, on top of the Phase 4 investment holdings, portfolio "
        "analytics, net worth history and financial goals, the Phase 3 recurring "
        "detection and cash flow forecasting, and the Phase 2 categorisation and "
        "budget engines."
    ),
)

# Middleware order is the reverse of registration order: whatever is added last
# ends up outermost. CORS must be outermost, so it is registered last — and the
# auth gate, which needs to sit *inside* it, is registered first.
#
# This is not stylistic. A 401 raised outside CORSMiddleware carries no
# Access-Control-Allow-Origin header, so the browser refuses to surface it and
# reports a generic "failed to fetch" instead. The real status never reaches the
# frontend and the fault looks like a CORS or network problem rather than a
# missing key. tests/test_api_key.py asserts the header is present on a 401.
app.add_middleware(ApiKeyMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    # Explicit, rather than "*": the wildcard is ignored by browsers whenever
    # allow_credentials is true, which would silently block the X-API-Key
    # preflight that the auth gate depends on.
    allow_headers=["Content-Type", "X-API-Key"],
)

app.include_router(plaid.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(categories.router)
app.include_router(rules.router)
app.include_router(budgets.router)
app.include_router(transfers.router)
app.include_router(recurring.router)
app.include_router(forecast.router)
app.include_router(investments.router)
app.include_router(net_worth.router)
app.include_router(goals.router)
app.include_router(research.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "plaid_env": settings.plaid_env}
