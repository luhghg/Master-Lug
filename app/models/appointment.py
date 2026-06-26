"""
Appointment models — shared booking infrastructure for TATTOO and future niches.
"""
import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Enum, ForeignKey,
    Index, Integer, SmallInteger, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApptBookingStatus(str, enum.Enum):
    PENDING             = "PENDING"              # questionnaire done, slot picked
    AWAITING_DEPOSIT    = "AWAITING_DEPOSIT"     # deposit instructions sent
    CONFIRMED           = "CONFIRMED"            # deposit confirmed by master
    COMPLETED           = "COMPLETED"            # session done
    CANCELLED_BY_CLIENT = "CANCELLED_BY_CLIENT"
    CANCELLED_BY_MASTER = "CANCELLED_BY_MASTER"
    NO_SHOW             = "NO_SHOW"
    RESCHEDULED         = "RESCHEDULED"          # replaced by new booking


class ApptDepositStatus(str, enum.Enum):
    WAITING         = "WAITING"          # waiting for client to pay
    SCREENSHOT_SENT = "SCREENSHOT_SENT"  # client sent screenshot
    CONFIRMED       = "CONFIRMED"        # master confirmed
    RETURNED        = "RETURNED"         # returned to client (master cancelled)
    KEPT            = "KEPT"             # kept by master (client cancelled late)


class ReminderType(str, enum.Enum):
    HOURS_168 = "7D"     # 7 days before
    HOURS_24  = "24H"
    HOURS_2   = "2H"
    REVIEW    = "REVIEW" # 3 days after session


class ReminderStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT    = "SENT"
    FAILED  = "FAILED"
    SKIPPED = "SKIPPED"


class ApptClient(Base):
    """Per-bot client CRM entry — created/updated on first booking."""
    __tablename__ = "appt_clients"
    __table_args__ = (
        UniqueConstraint("bot_id", "telegram_id", name="uq_appt_client"),
        Index("ix_appt_client_bot", "bot_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"), nullable=False
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int] = mapped_column(SmallInteger, default=5, server_default="5", nullable=False)
    bookings_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    cancellations_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    no_shows_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    first_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApptBooking(Base):
    """Full-lifecycle booking for TATTOO (and future appointment-based niches)."""
    __tablename__ = "appt_bookings"
    __table_args__ = (Index("ix_appt_booking_bot_date", "bot_id", "slot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appt_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Questionnaire
    style: Mapped[str | None] = mapped_column(String(128), nullable=True)
    body_zone: Mapped[str | None] = mapped_column(String(256), nullable=True)
    body_size: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    allergy_text: Mapped[str | None] = mapped_column(Text, nullable=True)   # None = no allergy
    overlap_text: Mapped[str | None] = mapped_column(Text, nullable=True)   # None = no overlap

    # Scheduling
    slot_date: Mapped[date] = mapped_column(Date, nullable=False)
    slot_time: Mapped[str] = mapped_column(String(5), nullable=False)        # "HH:MM"

    status: Mapped[ApptBookingStatus] = mapped_column(
        Enum(ApptBookingStatus), default=ApptBookingStatus.PENDING, nullable=False
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rescheduled_from_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("appt_bookings.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ApptDeposit(Base):
    __tablename__ = "appt_deposits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appt_bookings.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ApptDepositStatus] = mapped_column(
        Enum(ApptDepositStatus), default=ApptDepositStatus.WAITING, nullable=False
    )
    screenshot_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApptSchedule(Base):
    """Per-bot working schedule — which days, what hours, slot duration."""
    __tablename__ = "appt_schedules"
    __table_args__ = (
        UniqueConstraint("bot_id", "day_of_week", name="uq_appt_schedule"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)   # 0=Mon, 6=Sun
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)        # "09:00"
    end_time: Mapped[str] = mapped_column(String(5), nullable=False)          # "20:00"
    slot_duration_min: Mapped[int] = mapped_column(
        SmallInteger, default=60, server_default="60", nullable=False
    )
    buffer_min: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class ApptScheduleOverride(Base):
    """Per-date slot override — replaces generated schedule for a specific date.

    slots_json: JSON array of "HH:MM" strings, e.g. '["10:00","13:00","16:00"]'.
    When this row exists the slot generation is skipped entirely for that date.
    """
    __tablename__ = "appt_schedule_overrides"
    __table_args__ = (
        UniqueConstraint("bot_id", "date", name="uq_appt_schedule_override"),
        Index("ix_appt_sched_ovr_bot", "bot_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    slots_json: Mapped[str] = mapped_column(Text, nullable=False)  # '["10:00","12:00"]'


class ApptBlockedDate(Base):
    """Vacation or public holidays — entire date range blocked for bookings."""
    __tablename__ = "appt_blocked_dates"
    __table_args__ = (Index("ix_appt_blocked_bot", "bot_id", "date_start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("registered_bots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_end: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)


class ApptReminder(Base):
    """Scheduled reminders — background worker picks up PENDING rows."""
    __tablename__ = "appt_reminders"
    __table_args__ = (
        UniqueConstraint("booking_id", "reminder_type", name="uq_appt_reminder"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("appt_bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reminder_type: Mapped[ReminderType] = mapped_column(
        Enum(ReminderType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus), default=ReminderStatus.PENDING, nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
