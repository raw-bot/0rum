"""Expand optimizer_results.max_drawdown precision.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "optimizer_results",
        "max_drawdown",
        existing_type=sa.Numeric(8, 5),
        type_=sa.Numeric(12, 5),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "optimizer_results",
        "max_drawdown",
        existing_type=sa.Numeric(12, 5),
        type_=sa.Numeric(8, 5),
        existing_nullable=True,
    )
