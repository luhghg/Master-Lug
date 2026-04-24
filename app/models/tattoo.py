import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, Enum, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TattooStyle(str, enum.Enum):
    REALISM      = "REALISM"
    GRAPHIC      = "GRAPHIC"
    WATERCOLOR   = "WATERCOLOR"
    BLACKWORK    = "BLACKWORK"
    TRADITIONAL  = "TRADITIONAL"
    ORNAMENTAL   = "ORNAMENTAL"


class ReviewStatus(str, enum.Enum):
    PENDING  = "PENDING"
    APPROVED = "APPROVED"
    DELETED  = "DELETED"


class BookingStatus(str, enum.Enum):
    NEW       = "NEW"
    CANCELLED = "CANCELLED"


class TattooPortfolio(Base):
    __tablename__ = "tattoo_portfolio"
    __table_args__ = (Index("ix_portfolio_bot_style", "bot_id", "style"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    style: Mapped[str] = mapped_column(String(64), nullable=False)  # category key from BotConfig
    photo_id: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    work_time: Mapped[str] = mapped_column(String(128), nullable=False)
    price: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TattooReview(Base):
    __tablename__ = "tattoo_reviews"
    __table_args__ = (Index("ix_reviews_bot_status", "bot_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    photo_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus), default=ReviewStatus.PENDING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TattooBooking(Base):
    __tablename__ = "tattoo_bookings"
    __table_args__ = (Index("ix_bookings_bot_date", "bot_id", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idea: Mapped[str] = mapped_column(Text, nullable=False)
    body_part: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[str] = mapped_column(String(128), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)   # YYYY-MM-DD
    time_slot: Mapped[str] = mapped_column(String(5), nullable=False)  # HH:MM
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tattoo_portfolio.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus), default=BookingStatus.NEW, nullable=False
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BotSubscription(Base):
    """Every user who ever started a sub-bot — used for broadcast."""
    __tablename__ = "bot_subscriptions"
    __table_args__ = (
        UniqueConstraint("bot_id", "telegram_id", name="uq_bot_subscription"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
