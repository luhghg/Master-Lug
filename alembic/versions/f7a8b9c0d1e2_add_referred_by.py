"""add referred_by to registered_bots

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-05-18 14:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'registered_bots',
        sa.Column('referred_by', sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('registered_bots', 'referred_by')
