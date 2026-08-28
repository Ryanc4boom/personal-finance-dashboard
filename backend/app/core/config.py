from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://budget:budget@localhost:5432/budgeting"
    redis_url: str = "redis://localhost:6379/0"

    # Fernet key for encrypting Plaid access tokens at rest.
    encryption_key: str = ""

    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"
    plaid_products: str = "transactions"
    # Requested only where the institution supports them. `investments` belongs
    # here rather than in plaid_products because the two are enforced very
    # differently, and not at link_token_create — that call is accepted either
    # way. Products in `products` are treated as required, so Link hides every
    # institution that lacks them; requiring investments would quietly drop
    # Chase and Capital One from the bank picker. Leaving investments out
    # altogether has the opposite failure: a brokerage links fine but grants no
    # holdings, and the Phase 4 investment sync has nothing to read.
    plaid_optional_products: str = ""
    plaid_country_codes: str = "US"
    plaid_webhook_url: str = ""
    # Required to link OAuth institutions — which is most large US banks (Chase,
    # Wells Fargo, Capital One, BofA). Those hand the user off to the bank's own
    # site and need somewhere to send them back; without it Link fails on return
    # while smaller password-based institutions keep working, so the breakage
    # looks bank-specific rather than like a missing setting.
    #
    # Must be https in Production and registered under Developers -> API ->
    # Allowed Redirect URIs in the Plaid dashboard, so a bare
    # http://localhost:3000 will be rejected — use a tunnel (ngrok) for local
    # OAuth testing. Left blank the parameter is omitted entirely, which keeps
    # non-OAuth linking working exactly as before.
    plaid_redirect_uri: str = ""

    dev_user_email: str = "me@example.com"
    cors_origins: str = "http://localhost:3000"

    # Shared secret every API call must present in `X-API-Key`. This is not user
    # authentication — there is still exactly one user — it is a gate that stops
    # anything other than this app's own frontend from reaching the API.
    #
    # Two distinct holes close with it, and the second is the one that matters:
    #
    #  1. The API binds to localhost but is otherwise wide open, so any process
    #     on the machine can read every transaction.
    #  2. More seriously, a *custom request header* is not a CORS "simple
    #     request", so requiring one forces a preflight on every call. Without
    #     that, a POST carrying `text/plain` or a form content-type skips
    #     preflight entirely and reaches the handler — meaning any page the user
    #     happened to be browsing could fire `/api/v1/plaid/sync` and burn real
    #     Plaid quota. CORS only blocks *reading* the reply, never the side
    #     effect. See tests/test_api_key.py.
    #
    # Deliberately has no default: an auth gate that ships with a fallback value
    # is one someone forgets to override, and it fails open silently. Blank
    # means every request is refused with a 503 that says how to fix it.
    api_key: str = ""

    # ---- Rate limiting (see core/rate_limit.py) ----
    # Off only for tests that need to make many calls; never set this false in a
    # running deployment.
    rate_limit_enabled: bool = True
    # Generous. This bucket exists to stop a runaway render loop in the frontend
    # hammering the database, not to police a human clicking around.
    rate_limit_default_per_minute: int = 120
    # Deliberately small. Every request in this bucket costs a billed Plaid call
    # against a real quota, and there is no legitimate reason for a single user
    # to link or sync more than a handful of times a minute. A `useEffect` with a
    # bad dependency array can otherwise issue thousands.
    rate_limit_plaid_per_minute: int = 6
    # SEC enforces its 10 req/s ceiling by *IP ban*, and one research request
    # fans out into several filing downloads. Getting banned takes out the whole
    # research engine, so this is throttled well below what the app could push.
    rate_limit_research_per_minute: int = 20
    # Plaid retries webhooks on non-2xx, so this has to leave room for a genuine
    # retry storm while still capping a forged flood. Verification happens after
    # the limiter, so unsigned junk is cheap to shed here.
    rate_limit_webhook_per_minute: int = 60

    # ---- Error reporting (see core/telemetry.py) ----
    # Blank disables it entirely. Everything forwarded is scrubbed first — no
    # request bodies, no frame locals, no amounts — because the payloads in this
    # app are balances and merchant names.
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    # Performance tracing off by default. Spans carry SQL statements and their
    # bound parameters, which is exactly the data this app should not be
    # shipping off the machine; turn it on only with a scrubbed span processor.
    sentry_traces_sample_rate: float = 0.0

    # ---- Phase 5: SEC EDGAR + market data ----
    # SEC rejects requests without a declaring User-Agent that carries a contact
    # address (403, not 429), so this is not optional decoration. Left blank it
    # falls back to `dev_user_email`, which keeps the research engine working out
    # of the box rather than failing on a setting nobody was told to fill in.
    sec_user_agent: str = ""
    # SEC's published ceiling is 10 requests/second. Deliberately under it: the
    # limit is enforced by IP ban, and the research engine is not latency
    # critical enough to be worth flying close to it.
    sec_rate_limit_per_second: float = 6.0
    # Filings are immutable once accepted and company facts refresh at most
    # daily, so these are long on purpose — a re-run of the same ticker should
    # cost zero SEC requests.
    sec_facts_cache_seconds: int = 21_600  # 6h
    sec_document_cache_seconds: int = 604_800  # 7d — accepted filings never change
    # A 10-K primary document is normally 2-8MB of inline XBRL. Anything past
    # this is either an exhibit-laden monster or a mistake; truncating beats
    # holding it all in memory to regex over it.
    sec_max_document_bytes: int = 25_000_000

    # Optional quote provider. Without one, P/E and PEG report as "unknown"
    # rather than being fabricated — see services/market_data.py.
    finnhub_api_key: str = ""
    market_price_cache_seconds: int = 900  # 15m

    @property
    def sec_contact(self) -> str:
        """The User-Agent SEC sees. Must identify the app and a contact."""
        if self.sec_user_agent.strip():
            return self.sec_user_agent.strip()
        return f"Budgeting Platform Research/0.1 ({self.dev_user_email})"

    @property
    def plaid_product_list(self) -> list[str]:
        return [p.strip() for p in self.plaid_products.split(",") if p.strip()]

    @property
    def plaid_optional_product_list(self) -> list[str]:
        return [p.strip() for p in self.plaid_optional_products.split(",") if p.strip()]

    @property
    def plaid_country_code_list(self) -> list[str]:
        return [c.strip() for c in self.plaid_country_codes.split(",") if c.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
