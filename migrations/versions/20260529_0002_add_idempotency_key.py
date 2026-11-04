"""add idempotency key

Revision ID: 20260529_0002
Revises: 20260529_0001
Create Date: 2026-05-29 00:02:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260529_0002"
down_revision = "20260529_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_submissions_idempotency_key"),
        "submissions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_submissions_idempotency_key"), table_name="submissions")
    op.drop_column("submissions", "idempotency_key")
