"""baseline submissions table

Revision ID: 20260529_0001
Revises:
Create Date: 2026-05-29 00:01:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260529_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("form_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_submissions_email"), "submissions", ["email"], unique=False
    )
    op.create_index(op.f("ix_submissions_id"), "submissions", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_submissions_id"), table_name="submissions")
    op.drop_index(op.f("ix_submissions_email"), table_name="submissions")
    op.drop_table("submissions")
