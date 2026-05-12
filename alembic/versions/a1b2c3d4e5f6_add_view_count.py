"""add view_count to portfolio

Revision ID: a1b2c3d4e5f6
Revises: 8b63c747bca6
Create Date: 2026-05-08 12:00:00.000000
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '8b63c747bca6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tattoo_portfolio',
        sa.Column('view_count', sa.Integer(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('tattoo_portfolio', 'view_count')
