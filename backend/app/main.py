import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
