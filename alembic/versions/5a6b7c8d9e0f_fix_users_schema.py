"""fix users table: add id PK and missing columns

The initial migration created users with telegram_id as PK and no id column.
The ORM model expects id as PK, causing every handler to crash with
'column users.id does not exist' (caught silently -> bot sends nothing).

Revision ID: 5a6b7c8d9e0f
Revises: f7a8b9c0d1e2
Create Date: 2026-06-08 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '5a6b7c8d9e0f'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": column})
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists('users', 'id'):
        # Old schema: telegram_id is the primary key — replace with serial id
        op.execute(sa.text("ALTER TABLE users ADD COLUMN id SERIAL"))

        # Find the actual PK constraint name (may differ from users_pkey)
        bind = op.get_bind()
        row = bind.execute(sa.text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name='users' AND constraint_type='PRIMARY KEY' "
            "AND table_schema='public'"
        )).fetchone()
        if row:
            op.execute(sa.text(f'ALTER TABLE users DROP CONSTRAINT "{row[0]}"'))

        op.create_primary_key('pk_users', 'users', ['id'])
        op.create_unique_constraint('uq_users_telegram_id', 'users', ['telegram_id'])
        op.create_index('ix_users_telegram_id', 'users', ['telegram_id'])

    # Add every missing column idempotently
    op.execute(sa.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR(64)"))
    op.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "global_rating FLOAT NOT NULL DEFAULT 5.0"
    ))
    op.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "total_completed INTEGER NOT NULL DEFAULT 0"
    ))
    op.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "total_failed INTEGER NOT NULL DEFAULT 0"
    ))
    op.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "is_banned BOOLEAN NOT NULL DEFAULT false"
    ))
    op.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()"
    ))


def downgrade() -> None:
    pass
