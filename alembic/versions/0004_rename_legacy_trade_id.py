"""Rename legacy trade id column to broker_trade_id.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    has_legacy = _has_column("trades", "oanda_trade_id")
    has_broker = _has_column("trades", "broker_trade_id")

    if has_legacy and not has_broker:
        op.alter_column("trades", "oanda_trade_id", new_column_name="broker_trade_id")
    elif not has_broker:
        op.add_column("trades", sa.Column("broker_trade_id", sa.String(30), nullable=True))


def downgrade() -> None:
    if _has_column("trades", "broker_trade_id") and not _has_column("trades", "oanda_trade_id"):
        op.alter_column("trades", "broker_trade_id", new_column_name="oanda_trade_id")
