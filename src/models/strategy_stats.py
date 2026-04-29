"""StrategyStatsORM - lifetime per-strategy trading statistics (Phase 7 SIG-03).

One row per strategy (PK = strategy name). Cumulative lifetime totals.
Updated atomically in the same transaction as TradeORM status -> CLOSED.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class StrategyStatsORM(Base):
    """Per-strategy lifetime statistics for win rate, profit factor, and P&L.

    PK is the strategy name string (e.g. 'liquidity_sweep') - one row per strategy.
    Upserted via PostgreSQL INSERT ... ON CONFLICT (strategy) DO UPDATE.
    gross_profit_pct and gross_loss_pct store raw inputs so profit_factor
    can always be recomputed correctly (D-16).
    """

    __tablename__ = "strategy_stats"

    strategy: Mapped[str] = mapped_column(String(30), primary_key=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    wins: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    losses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    gross_profit_pct: Mapped[Decimal] = mapped_column(
        Numeric(12, 5), nullable=False, server_default="0"
    )
    gross_loss_pct: Mapped[Decimal] = mapped_column(
        Numeric(12, 5), nullable=False, server_default="0"
    )
    total_pnl_pct: Mapped[Decimal] = mapped_column(
        Numeric(12, 5), nullable=False, server_default="0"
    )
    win_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, server_default="0"
    )
    profit_factor: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
