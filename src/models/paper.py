"""Paper account snapshot ORM models."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, Numeric, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PaperAccountSnapshotORM(Base):
    """Point-in-time internal paper account state for dashboard/charting."""

    __tablename__ = "paper_account_snapshots"
    __table_args__ = (Index("idx_paper_account_snapshots_created", "created_at"),)

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
