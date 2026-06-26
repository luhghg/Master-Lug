"""Add appt_schedule_overrides for per-date slot management

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-26 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appt_schedule_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "bot_id", sa.Integer(),
            sa.ForeignKey("registered_bots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("slots_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("bot_id", "date", name="uq_appt_schedule_override"),
    )
    op.create_index("ix_appt_sched_ovr_bot", "appt_schedule_overrides", ["bot_id"])


def downgrade() -> None:
    op.drop_index("ix_appt_sched_ovr_bot", table_name="appt_schedule_overrides")
    op.drop_table("appt_schedule_overrides")
