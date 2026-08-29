"""Dagster code location for the analytics layer.

Orchestrates: Plaid ingestion (the app's own, imported rather than
reimplemented) -> dbt build -> dbt tests, on a schedule, idempotently.

Nothing in this package writes to `public`. dbt connects as the restricted
`budgeting_analytics` role, which has SELECT and no more; the ingestion asset
is the one exception and it goes through the app's own service layer rather
than issuing SQL of its own.
"""
