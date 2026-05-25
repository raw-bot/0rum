"""Add internal paper account snapshots.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_account_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cash_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(12, 2), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(12, 2), nullable=False),
        sa.Column("equity", sa.Numeric(12, 2), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index(
        "idx_paper_account_snapshots_created",
        "paper_account_snapshots",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_paper_account_snapshots_created", table_name="paper_account_snapshots")
    op.drop_table("paper_account_snapshots")
