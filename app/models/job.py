import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class JobStatus(str, enum.Enum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class JobType(str, enum.Enum):
    ONETIME   = "ONETIME"    # Разова робота — фіксована оплата
    PERMANENT = "PERMANENT"  # Постійна робота — місячна зарплата


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_city_status", "city", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    employer_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )

    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType), default=JobType.ONETIME, nullable=False
    )
    workers_needed: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    city: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    pay_description: Mapped[str] = mapped_column(String(512), nullable=False)
    location: Mapped[str] = mapped_column(String(256), nullable=False)
    scheduled_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.OPEN, nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Job {self.id} city={self.city} status={self.status}>"
