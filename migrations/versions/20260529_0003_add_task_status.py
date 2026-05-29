"""add task status table

Revision ID: 20260529_0003
Revises: 20260529_0002
Create Date: 2026-05-29 00:03:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260529_0003"
down_revision = "20260529_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_statuses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=50), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_task_statuses_task_id"),
        "task_statuses",
        ["task_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_statuses_task_id"), table_name="task_statuses")
    op.drop_table("task_statuses")
