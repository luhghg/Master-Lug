"""fix beauty tables: rename columns/tables to match ORM models

tattoo_portfolios → tattoo_portfolio
  file_id → photo_id, category → style, caption → description
  + add work_time, price

tattoo_reviews:
  reviewer_telegram_id → user_id
  + add user_name, photo_id
  + add DELETED to reviewstatus enum

tattoo_bookings:
  client_telegram_id → user_id, client_phone → phone
  + add idea, body_part, size, reference_id, cancel_reason

All operations are idempotent (check before rename/add).

Revision ID: 7c8d9e0f1a2b
Revises: 5a6b7c8d9e0f
Create Date: 2026-06-08 14:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '7c8d9e0f1a2b'
down_revision: Union[str, None] = '5a6b7c8d9e0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    r = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:n"
    ), {"n": name})
    return r.fetchone() is not None


def _col_exists(table: str, col: str) -> bool:
    bind = op.get_bind()
    r = bind.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
    ), {"t": table, "c": col})
    return r.fetchone() is not None


def _enum_val_exists(enum_name: str, val: str) -> bool:
    bind = op.get_bind()
    r = bind.execute(sa.text(
        "SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid=t.oid "
        "WHERE t.typname=:n AND e.enumlabel=:v"
    ), {"n": enum_name, "v": val})
    return r.fetchone() is not None


def upgrade() -> None:
    # ── tattoo_portfolios → tattoo_portfolio ──────────────────────────────────
    if _table_exists("tattoo_portfolios") and not _table_exists("tattoo_portfolio"):
        op.execute(sa.text("ALTER TABLE tattoo_portfolios RENAME TO tattoo_portfolio"))

    if _col_exists("tattoo_portfolio", "file_id"):
        op.execute(sa.text(
            "ALTER TABLE tattoo_portfolio RENAME COLUMN file_id TO photo_id"
        ))

    if _col_exists("tattoo_portfolio", "category"):
        op.execute(sa.text(
            "ALTER TABLE tattoo_portfolio RENAME COLUMN category TO style"
        ))

    if _col_exists("tattoo_portfolio", "caption"):
        op.execute(sa.text(
            "ALTER TABLE tattoo_portfolio RENAME COLUMN caption TO description"
        ))

    # Ensure description is NOT NULL (fill any NULLs first)
    if _col_exists("tattoo_portfolio", "description"):
        op.execute(sa.text(
            "UPDATE tattoo_portfolio SET description='' WHERE description IS NULL"
        ))
        op.execute(sa.text(
            "ALTER TABLE tattoo_portfolio ALTER COLUMN description SET NOT NULL"
        ))

    op.execute(sa.text(
        "ALTER TABLE tattoo_portfolio "
        "ADD COLUMN IF NOT EXISTS work_time VARCHAR(128) NOT NULL DEFAULT ''"
    ))
    op.execute(sa.text(
        "ALTER TABLE tattoo_portfolio "
        "ADD COLUMN IF NOT EXISTS price VARCHAR(128) NOT NULL DEFAULT ''"
    ))

    # ── tattoo_reviews ────────────────────────────────────────────────────────
    if _col_exists("tattoo_reviews", "reviewer_telegram_id"):
        op.execute(sa.text(
            "ALTER TABLE tattoo_reviews "
            "RENAME COLUMN reviewer_telegram_id TO user_id"
        ))

    op.execute(sa.text(
        "ALTER TABLE tattoo_reviews "
        "ADD COLUMN IF NOT EXISTS user_name VARCHAR(128)"
    ))
    op.execute(sa.text(
        "ALTER TABLE tattoo_reviews "
        "ADD COLUMN IF NOT EXISTS photo_id VARCHAR(256)"
    ))

    # Add DELETED value to reviewstatus enum
    if not _enum_val_exists("reviewstatus", "DELETED"):
        op.execute(sa.text("ALTER TYPE reviewstatus ADD VALUE 'DELETED'"))

    # ── tattoo_bookings ───────────────────────────────────────────────────────
    if _col_exists("tattoo_bookings", "client_telegram_id"):
        op.execute(sa.text(
            "ALTER TABLE tattoo_bookings "
            "RENAME COLUMN client_telegram_id TO user_id"
        ))

    if _col_exists("tattoo_bookings", "client_phone"):
        op.execute(sa.text(
            "ALTER TABLE tattoo_bookings "
            "RENAME COLUMN client_phone TO phone"
        ))

    op.execute(sa.text(
        "ALTER TABLE tattoo_bookings "
        "ADD COLUMN IF NOT EXISTS idea TEXT NOT NULL DEFAULT ''"
    ))
    op.execute(sa.text(
        "ALTER TABLE tattoo_bookings "
        "ADD COLUMN IF NOT EXISTS body_part VARCHAR(128) NOT NULL DEFAULT ''"
    ))
    op.execute(sa.text(
        "ALTER TABLE tattoo_bookings "
        "ADD COLUMN IF NOT EXISTS size VARCHAR(128) NOT NULL DEFAULT ''"
    ))
    op.execute(sa.text(
        "ALTER TABLE tattoo_bookings "
        "ADD COLUMN IF NOT EXISTS cancel_reason TEXT"
    ))

    # reference_id FK — add only after tattoo_portfolio is renamed
    op.execute(sa.text(
        "ALTER TABLE tattoo_bookings "
        "ADD COLUMN IF NOT EXISTS reference_id INTEGER "
        "REFERENCES tattoo_portfolio(id) ON DELETE SET NULL"
    ))


def downgrade() -> None:
    pass
