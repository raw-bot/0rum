"""Phase 7 Signal Mode: add trailing_stop_price to trades, create strategy_stats.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("trailing_stop_price", sa.Numeric(12, 5), nullable=True),
    )
    op.create_table(
        "strategy_stats",
        sa.Column("strategy", sa.String(30), primary_key=True),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gross_profit_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("gross_loss_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("total_pnl_pct", sa.Numeric(12, 5), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Numeric(6, 4), nullable=False, server_default="0"),
        sa.Column("profit_factor", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("strategy"),
    )


def downgrade() -> None:
    op.drop_table("strategy_stats")
    op.drop_column("trades", "trailing_stop_price")
