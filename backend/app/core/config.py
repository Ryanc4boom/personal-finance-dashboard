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
    def plaid_country_code_list(self) -> list[str]:
        return [c.strip() for c in self.plaid_country_codes.split(",") if c.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
