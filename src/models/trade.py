"""Trade ORM model — theoretical and live trade records."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class TradeORM(Base):
    """Trade record for both theoretical (signal mode) and live (auto mode) trades."""

    __tablename__ = "trades"
    __table_args__ = (
        Index("idx_trades_status", "status"),
        Index("idx_trades_opened", "opened_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    approved_signal_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("approved_signals.id"),
        nullable=False,
    )
    oanda_trade_id: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    sl_price: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    tp1_price: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    tp2_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 5), nullable=True)
    size_lots: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="OPEN")
    pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 5), nullable=True)
    pnl_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 5), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(nullable=False, server_default="NOW()")
    closed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    close_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
