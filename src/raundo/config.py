"""Typed configuration. Real secrets must never be committed."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(default="", validation_alias="BOT_TOKEN")
    bot_mode: str = Field(default="polling", validation_alias="BOT_MODE")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    admin_telegram_ids: str = Field(default="", validation_alias="ADMIN_TELEGRAM_IDS")
    database_url: str = Field(
        default="postgresql+asyncpg://raundo:raundo_local_only@localhost:5432/raundo",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    policy_version: str = Field(default="2026-10-22-draft", validation_alias="POLICY_VERSION")
    rate_limit_per_window: int = Field(default=30, validation_alias="RATE_LIMIT_PER_WINDOW")
    rate_limit_window_seconds: int = Field(default=10, validation_alias="RATE_LIMIT_WINDOW_SECONDS")
    webhook_base_url: str = Field(default="", validation_alias="WEBHOOK_BASE_URL")
    webhook_secret: str = Field(default="", validation_alias="WEBHOOK_SECRET")
    # Opt-in SQLAlchemy echo. Never auto-enable from ENVIRONMENT — dumps row values.
    sql_echo: bool = Field(default=False, validation_alias="SQL_ECHO")
    # Issue #5: audited test-grant entry for manual Premium testing. Default off,
    # admin-allowlist gated on top; never enabled in production automatically.
    premium_dev_grants_enabled: bool = Field(
        default=False, validation_alias="PREMIUM_DEV_GRANTS_ENABLED"
    )
    # Explicit local-live mode: the admin API may attach to the real bot DB
    # ("raundo") only when both flags below are set deliberately.
    raundo_env: str = Field(default="development", validation_alias="RAUNDO_ENV")
    allow_live_db: bool = Field(default=False, validation_alias="RAUNDO_ALLOW_LIVE_DB")
    # Start of the fixed 14-day ranking intervals (UTC ISO datetime).
    ranking_epoch_utc: str = Field(
        default="2026-10-01T00:00:00+00:00", validation_alias="RANKING_EPOCH_UTC"
    )

    # ------------------------------------------------------------------ payments
    # demo: UI/flow only, no charge and no activation; provider_test: YooKassa
    # test keys; live: real money (requires legal readiness and approved prices).
    payments_mode: str = Field(default="demo", validation_alias="PAYMENTS_MODE")
    payments_card_enabled: bool = Field(default=True, validation_alias="PAYMENTS_CARD_ENABLED")
    payments_stars_enabled: bool = Field(default=False, validation_alias="PAYMENTS_STARS_ENABLED")
    payments_prices_approved: bool = Field(
        default=False, validation_alias="PAYMENTS_PRICES_APPROVED"
    )
    payments_legal_ready: bool = Field(default=False, validation_alias="PAYMENTS_LEGAL_READY")
    payments_stars_prices_approved: bool = Field(
        default=False, validation_alias="PAYMENTS_STARS_PRICES_APPROVED"
    )
    checkout_base_url: str = Field(
        default="http://127.0.0.1:8090", validation_alias="CHECKOUT_BASE_URL"
    )
    yookassa_shop_id: str = Field(default="", validation_alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", validation_alias="YOOKASSA_SECRET_KEY")
    # Preferred production source: a Docker secret mounted read-only. The key is
    # never baked into the image, .env, logs or reports; the value is read on
    # demand and never stored on the settings object.
    yookassa_secret_key_file: str = Field(
        default="/run/secrets/yookassa_secret_key",
        validation_alias="YOOKASSA_SECRET_KEY_FILE",
    )
    yookassa_api_url: str = Field(
        default="https://api.yookassa.ru/v3", validation_alias="YOOKASSA_API_URL"
    )
    payment_seller_name: str = Field(default="", validation_alias="PAYMENT_SELLER_NAME")
    payment_seller_inn: str = Field(default="", validation_alias="PAYMENT_SELLER_INN")
    payment_seller_contact: str = Field(default="", validation_alias="PAYMENT_SELLER_CONTACT")
    payment_consent_version: str = Field(
        default="payments-2026-11-03-draft", validation_alias="PAYMENT_CONSENT_VERSION"
    )
    # HTTPS deployments must mark the admin session cookie Secure.
    admin_cookie_secure: bool = Field(default=False, validation_alias="ADMIN_COOKIE_SECURE")
    # Telegram Bot API egress. When set, every Bot API call goes through this
    # proxy (http://host:port or socks5://host:port) and never falls back to a
    # direct connection. TELEGRAM_REQUIRE_PROXY makes a missing proxy fatal.
    telegram_proxy: str = Field(default="", validation_alias="TELEGRAM_PROXY")
    telegram_require_proxy: bool = Field(default=False, validation_alias="TELEGRAM_REQUIRE_PROXY")
    # Telegram dedicated test environment (https://core.telegram.org/bots/features).
    # Every Bot API call then goes to .../bot<token>/test/<method>, so the test bot
    # can only see test users and pays with test Stars - real Stars are never spent.
    # Must stay false in production; require_valid_runtime() refuses the combination.
    telegram_test_mode: bool = Field(default=False, validation_alias="TELEGRAM_TEST_MODE")
    # The public /premium catalog shows real amounts only after the owner
    # approves the production price list. Until then it renders "на утверждении".
    premium_catalog_public: bool = Field(default=False, validation_alias="PREMIUM_CATALOG_PUBLIC")
    # Self-service purchase on the website. Stays off until YooKassa and Telegram approve.
    site_checkout_enabled: bool = Field(default=False, validation_alias="SITE_CHECKOUT_ENABLED")
    site_session_secret: str = Field(default="", validation_alias="SITE_SESSION_SECRET")

    def is_admin(self, telegram_user_id: int | None) -> bool:
        if telegram_user_id is None:
            return False
        known_ids: set[int] = set()
        for item in self.admin_telegram_ids.split(","):
            item = item.strip()
            if item and item.isdecimal():
                known_ids.add(int(item))
        return telegram_user_id in known_ids

    def yookassa_key(self) -> str:
        """Resolve the YooKassa secret without ever storing or logging it.

        Order: explicit env var (local development only) -> mounted Docker secret.
        A missing/unreadable file means "not configured"; it never raises and the
        value is not cached on the settings object.
        """
        direct = (self.yookassa_secret_key or "").strip()
        if direct:
            return direct
        path = (self.yookassa_secret_key_file or "").strip()
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def yookassa_configured(self) -> bool:
        return bool((self.yookassa_shop_id or "").strip() and self.yookassa_key())

    def require_valid_runtime(self) -> None:
        if not self.bot_token:
            raise RuntimeError("Missing BOT_TOKEN. Set it locally in .env, never GitHub.")
        if self.bot_mode != "polling":
            raise RuntimeError(
                "Only local polling is implemented. "
                "Do not claim webhook deployment; implement and test it first."
            )
        if not self.database_url:
            raise RuntimeError("Missing DATABASE_URL.")
        if not self.redis_url:
            raise RuntimeError("Missing REDIS_URL.")
        is_production = "production" in {
            self.environment.strip().lower(),
            self.raundo_env.strip().lower(),
        }
        if self.telegram_test_mode and is_production:
            raise RuntimeError(
                "TELEGRAM_TEST_MODE must never be enabled in production: "
                "the bot would poll test-environment users and test Stars."
            )
        # Real Stars may only be charged in one of two reviewed setups:
        #   1) Telegram test environment (TELEGRAM_TEST_MODE=true, test Stars, no money);
        #   2) live mode with the owner-approved prices and legal readiness.
        if self.payments_stars_enabled and not self.telegram_test_mode:
            if self.payments_mode != "live":
                raise RuntimeError(
                    "PAYMENTS_STARS_ENABLED outside live mode is only allowed with "
                    "TELEGRAM_TEST_MODE=true (Telegram dedicated test environment)."
                )
            if not (self.payments_stars_prices_approved and self.payments_legal_ready):
                raise RuntimeError(
                    "Live Telegram Stars require PAYMENTS_STARS_PRICES_APPROVED=true "
                    "and PAYMENTS_LEGAL_READY=true."
                )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
