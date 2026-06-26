"""add subscription_reminders table

Revision ID: a3b4c5d6e7f8
Revises: e6f7a8b9c0d1
Create Date: 2026-06-26 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'subscription_reminders',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bot_id', sa.Integer(), nullable=False, index=True),
        sa.Column('days_before', sa.Integer(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('bot_id', 'days_before', name='uq_sub_reminder'),
    )


def downgrade() -> None:
    op.drop_table('subscription_reminders')
