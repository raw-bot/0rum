"""Add candle source provenance columns.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candles", sa.Column("source_kind", sa.String(20), nullable=True))
    op.add_column("candles", sa.Column("research_source", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("candles", "research_source")
    op.drop_column("candles", "source_kind")
