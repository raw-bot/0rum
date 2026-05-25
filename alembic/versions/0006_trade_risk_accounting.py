"""Add trade risk accounting columns.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trades", sa.Column("equity_at_open", sa.Numeric(12, 2), nullable=True))
    op.add_column("trades", sa.Column("notional_usd", sa.Numeric(14, 2), nullable=True))
    op.add_column("trades", sa.Column("risk_amount_usd", sa.Numeric(12, 2), nullable=True))
    op.add_column("trades", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("idx_trades_expired_at", "trades", ["expired_at"])


def downgrade() -> None:
    op.drop_index("idx_trades_expired_at", table_name="trades")
    op.drop_column("trades", "expired_at")
    op.drop_column("trades", "risk_amount_usd")
    op.drop_column("trades", "notional_usd")
    op.drop_column("trades", "equity_at_open")
