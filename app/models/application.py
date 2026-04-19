import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApplicationStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        # One worker can apply to a job only once
        UniqueConstraint("job_id", "worker_telegram_id", name="uq_applications_job_worker"),
        Index("ix_applications_worker_telegram_id", "worker_telegram_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Зберігаємо telegram_id напряму — без FK на users.telegram_id,
    # бо PostgreSQL потребує PK/UNIQUE CONSTRAINT для FK (не просто index).
    # Цілісність забезпечується в коді через get_or_create_user.
    worker_telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus), default=ApplicationStatus.PENDING, nullable=False
    )

    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Application job={self.job_id} worker={self.worker_telegram_id} status={self.status}>"
