"""add idempotency claims table

Revision ID: 20261105_0004
Revises: 20260529_0003
Create Date: 2026-11-05 00:04:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261105_0004"
down_revision = "20260529_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_claims",
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("idempotency_claims")
