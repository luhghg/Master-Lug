"""fix tattoo_bookings legacy columns blocking INSERT

  - category: old NOT NULL column not in ORM model → drops constraint
  - client_name: already nullable, just ensure default

Revision ID: 8d9e0f1a2b3c
Revises: 7c8d9e0f1a2b
Create Date: 2026-06-08 21:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '8d9e0f1a2b3c'
down_revision: Union[str, None] = '7c8d9e0f1a2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _col_exists(table: str, col: str) -> bool:
    bind = op.get_bind()
    r = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": col})
    return r.fetchone() is not None


def upgrade() -> None:
    # category is a legacy NOT NULL column not present in the ORM model.
    # Any INSERT via SQLAlchemy omits it → PostgreSQL raises NotNullViolation.
    # Drop the constraint and give it a harmless default.
    if _col_exists("tattoo_bookings", "category"):
        op.execute(sa.text(
            "ALTER TABLE tattoo_bookings "
            "ALTER COLUMN category SET DEFAULT '', "
            "ALTER COLUMN category DROP NOT NULL"
        ))
        op.execute(sa.text(
            "UPDATE tattoo_bookings SET category='' WHERE category IS NULL"
        ))


def downgrade() -> None:
    pass
