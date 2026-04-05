"""OptimizerResult ORM model — walk-forward optimization results."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class OptimizerResultORM(Base):
    """Walk-forward optimizer result for a strategy parameter combination."""

    __tablename__ = "optimizer_results"
    __table_args__ = (
        Index("idx_optimizer_strategy", "strategy", "created_at"),
        Index("idx_optimizer_active", "is_active", postgresql_where=text("is_active = TRUE")),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    train_start: Mapped[datetime] = mapped_column(nullable=False)
    train_end: Mapped[datetime] = mapped_column(nullable=False)
    test_start: Mapped[datetime] = mapped_column(nullable=False)
    test_end: Mapped[datetime] = mapped_column(nullable=False)
    in_sample_score: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    oos_score: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    wfe: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    profit_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    sharpe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    win_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4), nullable=True)
    max_drawdown: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 5), nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default="NOW()")
