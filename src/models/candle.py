"""Candle ORM model — XAUUSD OHLCV data for 4 timeframes."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class Candle(Base):
    """OHLCV candle data for XAUUSD across M15, H1, H4, D1 timeframes."""

    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("instrument", "timeframe", "timestamp", name="uq_candle_instrument_tf_ts"),
        Index("idx_candles_tf_ts", "timeframe", "timestamp"),
        Index("idx_candles_instrument_tf", "instrument", "timeframe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument: Mapped[str] = mapped_column(String(10), nullable=False, default="XAUUSD")
    timeframe: Mapped[str] = mapped_column(String(5), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    research_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="NOW()",
    )
