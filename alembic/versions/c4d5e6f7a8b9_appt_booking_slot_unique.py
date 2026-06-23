"""Add partial unique index to prevent double-booking the same slot

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-23 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Only active bookings block a slot; cancelled/completed/rescheduled rows do not.
_WHERE = sa.text(
    "status NOT IN ("
    "'CANCELLED_BY_CLIENT', 'CANCELLED_BY_MASTER', "
    "'NO_SHOW', 'RESCHEDULED', 'COMPLETED'"
    ")"
)


def upgrade() -> None:
    op.create_index(
        "uq_appt_booking_slot",
        "appt_bookings",
        ["bot_id", "slot_date", "slot_time"],
        unique=True,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index("uq_appt_booking_slot", table_name="appt_bookings")
