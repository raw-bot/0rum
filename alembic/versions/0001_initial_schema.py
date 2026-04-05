"""Initial schema — all tables for 0rum.

Revision ID: 0001
Revises:
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # candles
    op.create_table(
        "candles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("instrument", sa.String(10), nullable=False, server_default="XAUUSD"),
        sa.Column("timeframe", sa.String(5), nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(12, 5), nullable=False),
        sa.Column("high", sa.Numeric(12, 5), nullable=False),
        sa.Column("low", sa.Numeric(12, 5), nullable=False),
        sa.Column("close", sa.Numeric(12, 5), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument", "timeframe", "timestamp", name="uq_candle_instrument_tf_ts"),
    )
    op.create_index("idx_candles_tf_ts", "candles", ["timeframe", sa.text("timestamp DESC")])
    op.create_index("idx_candles_instrument_tf", "candles", ["instrument", "timeframe"])

    # candidate_signals
    op.create_table(
        "candidate_signals",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("strategy", sa.String(30), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("entry_price", sa.Numeric(12, 5), nullable=False),
        sa.Column("sl_price", sa.Numeric(12, 5), nullable=False),
        sa.Column("tp1_price", sa.Numeric(12, 5), nullable=False),
        sa.Column("tp2_price", sa.Numeric(12, 5), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("timeframe", sa.String(5), nullable=False),
        sa.Column("params_snapshot", JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("status", sa.String(15), nullable=False, server_default="PENDING"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_candidate_created", "candidate_signals", [sa.text("created_at DESC")])
    op.create_index("idx_candidate_status", "candidate_signals", ["status"])

    # approved_signals
    op.create_table(
        "approved_signals",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("candidate_signal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rank_score", sa.Numeric(6, 4), nullable=False),
        sa.Column("risk_check_passed", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("execution_status", sa.String(15), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["candidate_signal_id"], ["candidate_signals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # trades
    op.create_table(
        "trades",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("approved_signal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("oanda_trade_id", sa.String(30), nullable=True),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("entry_price", sa.Numeric(12, 5), nullable=False),
        sa.Column("sl_price", sa.Numeric(12, 5), nullable=False),
        sa.Column("tp1_price", sa.Numeric(12, 5), nullable=False),
        sa.Column("tp2_price", sa.Numeric(12, 5), nullable=True),
        sa.Column("size_lots", sa.Numeric(8, 4), nullable=False),
        sa.Column("status", sa.String(15), nullable=False, server_default="OPEN"),
        sa.Column("pnl", sa.Numeric(12, 5), nullable=True),
        sa.Column("pnl_pct", sa.Numeric(8, 5), nullable=True),
        sa.Column("opened_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(20), nullable=True),
        sa.ForeignKeyConstraint(["approved_signal_id"], ["approved_signals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_trades_status", "trades", ["status"])
    op.create_index("idx_trades_opened", "trades", [sa.text("opened_at DESC")])

    # optimizer_results
    op.create_table(
        "optimizer_results",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("strategy", sa.String(30), nullable=False),
        sa.Column("params", JSONB(), nullable=False),
        sa.Column("train_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("train_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("test_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("test_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("in_sample_score", sa.Numeric(8, 5), nullable=False),
        sa.Column("oos_score", sa.Numeric(8, 5), nullable=False),
        sa.Column("wfe", sa.Numeric(6, 4), nullable=False),
        sa.Column("profit_factor", sa.Numeric(8, 4), nullable=True),
        sa.Column("sharpe_ratio", sa.Numeric(8, 4), nullable=True),
        sa.Column("win_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(8, 5), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_optimizer_strategy", "optimizer_results", ["strategy", sa.text("created_at DESC")])
    op.create_index(
        "idx_optimizer_active",
        "optimizer_results",
        ["is_active"],
        postgresql_where=sa.text("is_active = TRUE"),
    )

    # market_regimes
    op.create_table(
        "market_regimes",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("regime", sa.String(15), nullable=False),
        sa.Column("atr_value", sa.Numeric(12, 5), nullable=False),
        sa.Column("atr_pctile", sa.Numeric(6, 4), nullable=False),
        sa.Column("adx_value", sa.Numeric(8, 4), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_regime_ts", "market_regimes", [sa.text("timestamp DESC")])


def downgrade() -> None:
    op.drop_table("market_regimes")
    op.drop_table("optimizer_results")
    op.drop_table("trades")
    op.drop_table("approved_signals")
    op.drop_table("candidate_signals")
    op.drop_table("candles")
