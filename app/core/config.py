from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Master Bot ---
    MASTER_BOT_TOKEN: str

    # --- Database ---
    DATABASE_URL: str  # postgresql+asyncpg://user:pass@host:5432/db

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Webhook Security ---
    BASE_WEBHOOK_URL: str          # https://yourdomain.com
    SECRET_WEBHOOK_TOKEN: str      # Random secret sent in X-Telegram-Bot-Api-Secret-Token

    # --- Token Encryption ---
    ENCRYPTION_KEY: str            # Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

    # --- Platform Owner ---
    PLATFORM_OWNER_ID: int = 0     # Telegram ID of the platform developer/owner

    # --- Rate Limiting ---
    RATE_LIMIT_REQUESTS: int = 10
    RATE_LIMIT_WINDOW: int = 60    # seconds

    # --- Monitoring ---
    SENTRY_DSN: str = ""           # Leave empty to disable Sentry

    # --- Support ---
    SUPPORT_USERNAME: str = ""       # Telegram username for support button (without @)

    # --- Subscription / payments ---
    MONOBANK_CARD: str = ""          # Card number shown in payment reminders
    MONOBANK_API_TOKEN: str = ""     # From Monobank app → Settings → Other → API
    SUBSCRIPTION_PRICE: int = 299    # Beauty bot UAH per month
    SUBSCRIPTION_PRICE_LABOR: int = 79  # Labor bot UAH per month

    # --- Demo bots (register them on the platform, then fill in) ---
    DEMO_BOT_LABOR: str = ""            # @username — shown as link on landing
    DEMO_BOT_BEAUTY: str = ""           # @username — shown as link on landing
    DEMO_BOT_TATTOO: str = ""           # @username — shown as link on landing
    DEMO_BOT_LABOR_ID: int = 0          # registered_bot.id — for demo mode logic
    DEMO_BOT_BEAUTY_ID: int = 0         # registered_bot.id — for demo mode logic
    DEMO_BOT_TATTOO_ID: int = 0         # registered_bot.id — for demo mode logic
    DEMO_BOT_TATTOO_TOKEN: str = ""     # Bot token — starts polling demo bot in background


def niche_price(niche) -> int:
    """Return subscription price in UAH for the given BotNiche."""
    s = get_settings()
    from app.models.bot import BotNiche
    if niche == BotNiche.LABOR:
        return s.SUBSCRIPTION_PRICE_LABOR
    return s.SUBSCRIPTION_PRICE


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
