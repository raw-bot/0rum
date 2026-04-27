"""Risk gate query helpers — read-only against TradeORM.

Per D-01: TradeORM is the single source of truth for open positions, daily P&L,
and consecutive stops. Phase 6 NEVER inserts/updates TradeORM — Phase 7 will.
Empty TradeORM is a valid state (gates pass cleanly via func.coalesce).
Per Pitfall 4: gates accept an explicit AsyncSession — they do NOT open
AsyncSessionLocal internally; the caller (RiskGateRunner / health endpoint)
owns the session lifecycle.
"""

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.trade import TradeORM

log = structlog.get_logger(__name__)


async def evaluate_daily_loss(
    session: AsyncSession, daily_loss_limit: float
) -> tuple[bool, float]:
    """RISK-01 — return (passed, daily_pnl_pct).

    passed=True iff daily_pnl_pct > daily_loss_limit.
    Empty trades → coalesced SUM is 0.0 → passed=True (Pitfall 3).
    UTC-day boundary computed server-side to avoid app-clock skew (D-14).
    """
    stmt = select(
        func.coalesce(func.sum(TradeORM.pnl_pct), 0)
    ).where(
        TradeORM.closed_at >= func.date_trunc("day", func.timezone("UTC", func.now())),
        TradeORM.status == "CLOSED",
    )
    result = await session.execute(stmt)
    daily_pnl_pct = float(result.scalar_one())
    passed = daily_pnl_pct > daily_loss_limit
    return passed, daily_pnl_pct


async def evaluate_max_positions(
    session: AsyncSession, max_positions: int
) -> tuple[bool, int]:
    """RISK-02 — return (passed, open_count).

    passed=True iff open_count < max_positions.
    Empty TradeORM → 0 → passed=True.
    """
    stmt = select(func.count()).select_from(TradeORM).where(TradeORM.status == "OPEN")
    result = await session.execute(stmt)
    open_count = int(result.scalar_one())
    passed = open_count < max_positions
    return passed, open_count


async def count_same_direction_open(
    session: AsyncSession, direction: str
) -> int:
    """RISK-03 — return count of OPEN trades in the given direction (BUY or SELL).

    The 4+ threshold lives in the sizer (per D-04), not here.
    direction comes from CandidateSignal.direction.value (enum-validated upstream).
    """
    stmt = (
        select(func.count())
        .select_from(TradeORM)
        .where(TradeORM.status == "OPEN", TradeORM.direction == direction)
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_open_positions(session: AsyncSession) -> int:
    """Thin wrapper for /health endpoint — returns just the count, no threshold."""
    stmt = select(func.count()).select_from(TradeORM).where(TradeORM.status == "OPEN")
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_daily_pnl_pct(session: AsyncSession) -> float:
    """Thin wrapper for /health endpoint — returns just the daily P&L pct, no threshold."""
    stmt = select(
        func.coalesce(func.sum(TradeORM.pnl_pct), 0)
    ).where(
        TradeORM.closed_at >= func.date_trunc("day", func.timezone("UTC", func.now())),
        TradeORM.status == "CLOSED",
    )
    result = await session.execute(stmt)
    return float(result.scalar_one())
