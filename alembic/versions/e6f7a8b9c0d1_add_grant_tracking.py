"""Add last_grant_at and last_grant_days to registered_bots

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-26 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("registered_bots", sa.Column("last_grant_at",   sa.DateTime(timezone=True), nullable=True))
    op.add_column("registered_bots", sa.Column("last_grant_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("registered_bots", "last_grant_days")
    op.drop_column("registered_bots", "last_grant_at")
