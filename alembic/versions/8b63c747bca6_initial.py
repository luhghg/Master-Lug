"""initial

Revision ID: 8b63c747bca6
Revises: 
Create Date: 2026-04-25 15:04:04.818952

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '8b63c747bca6'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('first_name', sa.String(), nullable=True),
        sa.Column('last_name', sa.String(), nullable=True),
        sa.Column('terms_agreed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('telegram_id'),
    )

    op.create_table(
        'registered_bots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('owner_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('bot_username', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('encrypted_token', sa.String(), nullable=False),
        sa.Column('niche', sa.Enum('LABOR', 'BEAUTY', 'SPORTS', name='botniche'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bot_username'),
        sa.UniqueConstraint('token_hash'),
    )

    op.create_table(
        'bot_configs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['registered_bots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bot_id', 'key', name='uq_bot_config'),
    )

    op.create_table(
        'bot_subscriptions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('subscribed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['registered_bots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bot_id', 'telegram_id', name='uq_bot_subscription'),
    )

    op.create_table(
        'platform_whitelist',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('full_name', sa.String(), nullable=True),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_id'),
    )

    op.create_table(
        'jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('employer_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('job_type', sa.Enum('ONETIME', 'PERMANENT', name='jobtype'), nullable=False),
        sa.Column('status', sa.Enum('OPEN', 'ASSIGNED', 'COMPLETED', 'CANCELLED', name='jobstatus'), nullable=False, server_default='OPEN'),
        sa.Column('city', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('pay_description', sa.String(), nullable=False),
        sa.Column('workers_needed', sa.Integer(), nullable=False),
        sa.Column('location', sa.String(), nullable=False),
        sa.Column('scheduled_time', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['registered_bots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_jobs_bot_id_status', 'jobs', ['bot_id', 'status'])

    op.create_table(
        'applications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('worker_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'ACCEPTED', 'REJECTED', name='applicationstatus'), nullable=False, server_default='PENDING'),
        sa.Column('applied_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'bot_blocked_users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('blocked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['registered_bots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bot_id', 'telegram_id', name='uq_bot_blocked_user'),
    )

    op.create_table(
        'tattoo_portfolios',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('file_id', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('caption', sa.String(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['registered_bots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'tattoo_reviews',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('reviewer_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('booking_id', sa.Integer(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', name='reviewstatus'), nullable=False, server_default='PENDING'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['registered_bots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'tattoo_bookings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bot_id', sa.Integer(), nullable=False),
        sa.Column('client_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('client_name', sa.String(), nullable=True),
        sa.Column('client_phone', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('time_slot', sa.String(), nullable=False),
        sa.Column('status', sa.Enum('NEW', 'CONFIRMED', 'CANCELLED', 'DONE', name='bookingstatus'), nullable=False, server_default='NEW'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bot_id'], ['registered_bots.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('tattoo_bookings')
    op.drop_table('tattoo_reviews')
    op.drop_table('tattoo_portfolios')
    op.drop_table('bot_blocked_users')
    op.drop_table('applications')
    op.drop_index('ix_jobs_bot_id_status', table_name='jobs')
    op.drop_table('jobs')
    op.drop_table('platform_whitelist')
    op.drop_table('bot_subscriptions')
    op.drop_table('bot_configs')
    op.drop_table('registered_bots')
    op.drop_table('users')
    op.execute("DROP TYPE IF EXISTS bookingstatus")
    op.execute("DROP TYPE IF EXISTS reviewstatus")
    op.execute("DROP TYPE IF EXISTS applicationstatus")
    op.execute("DROP TYPE IF EXISTS jobstatus")
    op.execute("DROP TYPE IF EXISTS jobtype")
    op.execute("DROP TYPE IF EXISTS botniche")
