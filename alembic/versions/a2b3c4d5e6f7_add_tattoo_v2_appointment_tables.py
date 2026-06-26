"""Add TATTOO niche v2 appointment tables

Revision ID: a2b3c4d5e6f7
Revises: 8d9e0f1a2b3c
Create Date: 2026-06-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = '8d9e0f1a2b3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ENUM type names
_BOOKING_STATUS = sa.Enum(
    'PENDING', 'AWAITING_DEPOSIT', 'CONFIRMED', 'COMPLETED',
    'CANCELLED_BY_CLIENT', 'CANCELLED_BY_MASTER', 'NO_SHOW', 'RESCHEDULED',
    name='apptbookingstatus',
)
_DEPOSIT_STATUS = sa.Enum(
    'WAITING', 'SCREENSHOT_SENT', 'CONFIRMED', 'RETURNED', 'KEPT',
    name='apptdepositstatus',
)
_REMINDER_TYPE = sa.Enum(
    '7D', '24H', '2H', 'REVIEW',
    name='remindertype',
)
_REMINDER_STATUS = sa.Enum(
    'PENDING', 'SENT', 'FAILED', 'SKIPPED',
    name='reminderstatus',
)


def upgrade() -> None:
    # PG 9.1+ allows ALTER TYPE ADD VALUE inside a transaction; use op.execute directly
    op.execute(sa.text("ALTER TYPE botniche ADD VALUE IF NOT EXISTS 'TATTOO'"))

    # appt_clients
    op.create_table(
        'appt_clients',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('registered_bots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(128), nullable=True),
        sa.Column('full_name', sa.String(256), nullable=True),
        sa.Column('phone', sa.String(32), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('rating', sa.SmallInteger(), server_default='5', nullable=False),
        sa.Column('bookings_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cancellations_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('no_shows_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_blocked', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('first_contact_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('bot_id', 'telegram_id', name='uq_appt_client'),
    )
    op.create_index('ix_appt_client_bot', 'appt_clients', ['bot_id'])

    # appt_bookings
    op.create_table(
        'appt_bookings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('registered_bots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('appt_clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('style', sa.String(128), nullable=True),
        sa.Column('body_zone', sa.String(256), nullable=True),
        sa.Column('body_size', sa.String(64), nullable=True),
        sa.Column('reference_text', sa.Text(), nullable=True),
        sa.Column('reference_file_id', sa.String(256), nullable=True),
        sa.Column('allergy_text', sa.Text(), nullable=True),
        sa.Column('overlap_text', sa.Text(), nullable=True),
        sa.Column('slot_date', sa.Date(), nullable=False),
        sa.Column('slot_time', sa.String(5), nullable=False),
        sa.Column('status', _BOOKING_STATUS, nullable=False, server_default='PENDING'),
        sa.Column('cancel_reason', sa.Text(), nullable=True),
        sa.Column('rescheduled_from_id', sa.Integer(),
                  sa.ForeignKey('appt_bookings.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_appt_booking_bot_date', 'appt_bookings', ['bot_id', 'slot_date'])
    op.create_index('ix_appt_bookings_bot_id', 'appt_bookings', ['bot_id'])
    op.create_index('ix_appt_bookings_client_id', 'appt_bookings', ['client_id'])

    # appt_deposits
    op.create_table(
        'appt_deposits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('booking_id', sa.Integer(),
                  sa.ForeignKey('appt_bookings.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('status', _DEPOSIT_STATUS, nullable=False, server_default='WAITING'),
        sa.Column('screenshot_file_id', sa.String(256), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('refund_reason', sa.Text(), nullable=True),
    )
    op.create_index('ix_appt_deposits_booking_id', 'appt_deposits', ['booking_id'])

    # appt_schedules
    op.create_table(
        'appt_schedules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('registered_bots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('day_of_week', sa.SmallInteger(), nullable=False),
        sa.Column('start_time', sa.String(5), nullable=False),
        sa.Column('end_time', sa.String(5), nullable=False),
        sa.Column('slot_duration_min', sa.SmallInteger(), server_default='60', nullable=False),
        sa.Column('buffer_min', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.UniqueConstraint('bot_id', 'day_of_week', name='uq_appt_schedule'),
    )
    op.create_index('ix_appt_schedules_bot_id', 'appt_schedules', ['bot_id'])

    # appt_blocked_dates
    op.create_table(
        'appt_blocked_dates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), sa.ForeignKey('registered_bots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('date_start', sa.Date(), nullable=False),
        sa.Column('date_end', sa.Date(), nullable=False),
        sa.Column('reason', sa.String(256), nullable=True),
    )
    op.create_index('ix_appt_blocked_bot', 'appt_blocked_dates', ['bot_id', 'date_start'])

    # appt_reminders
    op.create_table(
        'appt_reminders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('booking_id', sa.Integer(),
                  sa.ForeignKey('appt_bookings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reminder_type', _REMINDER_TYPE, nullable=False),
        sa.Column('status', _REMINDER_STATUS, nullable=False, server_default='PENDING'),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('booking_id', 'reminder_type', name='uq_appt_reminder'),
    )
    op.create_index('ix_appt_reminders_booking_id', 'appt_reminders', ['booking_id'])


def downgrade() -> None:
    op.drop_table('appt_reminders')
    op.drop_table('appt_blocked_dates')
    op.drop_table('appt_schedules')
    op.drop_table('appt_deposits')
    op.drop_table('appt_bookings')
    op.drop_table('appt_clients')

    _BOOKING_STATUS.drop(op.get_bind(), checkfirst=True)
    _DEPOSIT_STATUS.drop(op.get_bind(), checkfirst=True)
    _REMINDER_TYPE.drop(op.get_bind(), checkfirst=True)
    _REMINDER_STATUS.drop(op.get_bind(), checkfirst=True)
    # Note: TATTOO cannot be removed from PostgreSQL enum via ALTER TYPE
