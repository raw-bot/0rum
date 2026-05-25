"""Risk gate query helpers — read-only against TradeORM.

Per D-01: TradeORM is the single source of truth for open positions, daily P&L,
and consecutive stops. Phase 6 NEVER inserts/updates TradeORM — Phase 7 will.
Empty TradeORM is a valid state (gates pass cleanly via func.coalesce).
Per Pitfall 4: gates accept an explicit AsyncSession — they do NOT open
AsyncSessionLocal internally; the caller (RiskGateRunner / health endpoint)
owns the session lifecycle.
"""

from decimal import Decimal

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.trade import TradeORM
from src.risk.account import get_daily_equity_pnl_pct

log = structlog.get_logger(__name__)

ACTIVE_POSITION_STATUSES = ("OPEN", "TP1_HIT")


async def evaluate_daily_loss(
    session: AsyncSession,
    daily_loss_limit: float | Decimal,
    fallback_equity: Decimal | None = None,
) -> tuple[bool, Decimal]:
    """RISK-01 — return (passed, daily_pnl_pct).

    passed=True iff daily_pnl_pct > daily_loss_limit.
    Empty trades → coalesced SUM is 0.0 → passed=True (Pitfall 3).
    Daily P&L is now equity-return based: money P&L / equity_at_open.
    """
    if fallback_equity is None:
        fallback_equity = get_settings().theoretical_equity_usd

    daily_pnl_pct = await get_daily_equity_pnl_pct(session, fallback_equity)
    passed = daily_pnl_pct > Decimal(str(daily_loss_limit))
    return passed, daily_pnl_pct


async def evaluate_max_positions(
    session: AsyncSession, max_positions: int
) -> tuple[bool, int]:
    """RISK-02 — return (passed, open_count).

    passed=True iff open_count < max_positions.
    Empty TradeORM → 0 → passed=True.
    """
    stmt = (
        select(func.count())
        .select_from(TradeORM)
        .where(TradeORM.status.in_(ACTIVE_POSITION_STATUSES))
    )
    result = await session.execute(stmt)
    open_count = int(result.scalar_one())
    passed = open_count < max_positions
    return passed, open_count


async def count_same_direction_open(
    session: AsyncSession, direction: str
) -> int:
    """RISK-03 — return active trade count in the given direction (BUY or SELL).

    The 4+ threshold lives in the sizer (per D-04), not here.
    direction comes from CandidateSignal.direction.value (enum-validated upstream).
    """
    stmt = (
        select(func.count())
        .select_from(TradeORM)
        .where(
            TradeORM.status.in_(ACTIVE_POSITION_STATUSES),
            TradeORM.direction == direction,
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_open_positions(session: AsyncSession) -> int:
    """Thin wrapper for /health endpoint — returns just the count, no threshold."""
    stmt = (
        select(func.count())
        .select_from(TradeORM)
        .where(TradeORM.status.in_(ACTIVE_POSITION_STATUSES))
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_daily_pnl_pct(session: AsyncSession) -> float:
    """Thin wrapper for /health endpoint — returns just the daily P&L pct, no threshold."""
    settings = get_settings()
    return float(
        await get_daily_equity_pnl_pct(
            session,
            fallback_equity=settings.theoretical_equity_usd,
        )
    )
