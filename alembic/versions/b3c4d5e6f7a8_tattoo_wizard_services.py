"""Add wizard columns to tattoo_services

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-06-20 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tattoo_services', sa.Column('price_from', sa.Integer(), nullable=True))
    op.add_column('tattoo_services', sa.Column('price_to', sa.Integer(), nullable=True))
    op.add_column('tattoo_services', sa.Column('description', sa.Text(), nullable=True))
    op.add_column('tattoo_services', sa.Column(
        'is_active', sa.Boolean(), server_default='true', nullable=False
    ))


def downgrade() -> None:
    op.drop_column('tattoo_services', 'is_active')
    op.drop_column('tattoo_services', 'description')
    op.drop_column('tattoo_services', 'price_to')
    op.drop_column('tattoo_services', 'price_from')
