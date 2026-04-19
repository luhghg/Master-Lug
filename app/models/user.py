from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    """
    Central user table shared across ALL sub-bots.
    global_rating is the cross-platform reputation score.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Worker preference — indexed for city-based job filtering
    city: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # ── Global Reputation System ──────────────────────────────────────────────
    global_rating: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    total_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # ─────────────────────────────────────────────────────────────────────────

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # GDPR: explicit consent timestamp — NULL means not yet agreed
    terms_agreed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<User tg={self.telegram_id} rating={self.global_rating}>"
