import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BotNiche(str, enum.Enum):
    """Plug-in niches — add a new value to expand the platform."""
    LABOR = "LABOR"
    BEAUTY = "BEAUTY"
    SPORTS = "SPORTS"


class RegisteredBot(Base):
    """
    Every sub-bot registered on the platform.
    token_hash: SHA-256 of the plain token — used for O(1) DB lookup.
    encrypted_token: Fernet-encrypted token — used to recover token on restart.
    """

    __tablename__ = "registered_bots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_telegram_id: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False
    )

    # Security: never store plain tokens
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    encrypted_token: Mapped[str] = mapped_column(String(512), nullable=False)

    bot_username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    niche: Mapped[BotNiche] = mapped_column(
        Enum(BotNiche), default=BotNiche.LABOR, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RegisteredBot @{self.bot_username} niche={self.niche}>"
