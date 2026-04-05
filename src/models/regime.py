"""MarketRegime ORM model — market regime detection records."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class MarketRegimeORM(Base):
    """Market regime classification record."""

    __tablename__ = "market_regimes"
    __table_args__ = (
        Index("idx_regime_ts", "timestamp"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    timestamp: Mapped[datetime] = mapped_column(nullable=False)
    regime: Mapped[str] = mapped_column(String(15), nullable=False)
    atr_value: Mapped[Decimal] = mapped_column(Numeric(12, 5), nullable=False)
    atr_pctile: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    adx_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default="NOW()")
