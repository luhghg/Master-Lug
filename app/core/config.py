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

    # --- Rate Limiting ---
    RATE_LIMIT_REQUESTS: int = 10
    RATE_LIMIT_WINDOW: int = 60    # seconds


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
