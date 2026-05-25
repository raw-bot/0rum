"""Internal paper account persistence and dashboard helpers."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database import AsyncSessionLocal
from src.execution.paper.account import (
    PaperAccountState,
    PaperExposureState,
    compute_paper_account_state,
    compute_paper_exposure_state,
)
from src.models.candle import Candle
from src.models.paper import PaperAccountSnapshotORM
from src.models.trade import TradeORM


async def get_latest_mark_price(session: AsyncSession) -> Decimal | None:
    """Return latest completed M15 close used as paper mark price."""

    stmt = (
        select(Candle.close)
        .where(
            Candle.instrument == "XAUUSD",
            Candle.timeframe == "M15",
            Candle.complete.is_(True),
        )
        .order_by(Candle.timestamp.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    close = result.scalar_one_or_none()
    return Decimal(str(close)) if close is not None else None


async def compute_current_paper_state(
    session: AsyncSession,
    *,
    starting_balance: Decimal,
    mark_price: Decimal | None = None,
) -> PaperAccountState:
    """Compute current paper account state from persisted trades."""

    if mark_price is None:
        mark_price = await get_latest_mark_price(session)

    result = await session.execute(select(TradeORM))
    trades = result.scalars().all()
    return compute_paper_account_state(
        starting_balance=starting_balance,
        trades=trades,
        mark_price=mark_price,
    )


async def compute_current_paper_exposure(
    session: AsyncSession,
    *,
    starting_balance: Decimal,
) -> PaperExposureState:
    """Compute current paper exposure metrics from persisted trades."""

    result = await session.execute(select(TradeORM))
    trades = result.scalars().all()
    return compute_paper_exposure_state(
        starting_balance=starting_balance,
        trades=trades,
    )


def paper_account_payload(
    *,
    starting_balance: Decimal,
    state: PaperAccountState,
    exposure: PaperExposureState,
) -> dict:
    """Serialize paper account state for API responses."""

    return {
        "starting_balance_usd": float(starting_balance),
        "cash_balance_usd": float(state.cash_balance),
        "equity_usd": float(state.equity),
        "realized_pnl_usd": float(state.realized_pnl),
        "unrealized_pnl_usd": float(state.unrealized_pnl),
        "open_positions": state.open_positions,
        "closed_trades_missing_pnl": state.closed_trades_missing_pnl,
        "notional_exposure_usd": float(exposure.notional_exposure),
        "stop_risk_usd": float(exposure.stop_risk),
        "total_lots": float(exposure.total_lots),
        "exposure_multiple": float(exposure.exposure_multiple),
        "stop_risk_pct": float(exposure.stop_risk_pct),
    }


async def get_equity_curve(
    session: AsyncSession,
    *,
    current_state: PaperAccountState,
    limit: int = 200,
) -> list[dict]:
    """Return oldest-first equity snapshot points for dashboard charting."""

    stmt = (
        select(PaperAccountSnapshotORM)
        .order_by(PaperAccountSnapshotORM.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(reversed(result.scalars().all()))
    curve = [
        {
            "timestamp": row.created_at.isoformat() if row.created_at else None,
            "equity_usd": float(row.equity),
            "cash_balance_usd": float(row.cash_balance),
            "unrealized_pnl_usd": float(row.unrealized_pnl),
            "open_positions": row.open_positions,
        }
        for row in rows
    ]
    if not curve:
        curve.append({
            "timestamp": None,
            "equity_usd": float(current_state.equity),
            "cash_balance_usd": float(current_state.cash_balance),
            "unrealized_pnl_usd": float(current_state.unrealized_pnl),
            "open_positions": current_state.open_positions,
        })
    return curve


async def record_paper_account_snapshot() -> None:
    """Persist the current internal paper account state for equity charting."""

    settings = get_settings()
    async with AsyncSessionLocal() as session:
        state = await compute_current_paper_state(
            session,
            starting_balance=settings.theoretical_equity_usd,
        )
        exposure = await compute_current_paper_exposure(
            session,
            starting_balance=settings.theoretical_equity_usd,
        )
        session.add(
            PaperAccountSnapshotORM(
                cash_balance=state.cash_balance,
                realized_pnl=state.realized_pnl,
                unrealized_pnl=state.unrealized_pnl,
                equity=state.equity,
                open_positions=state.open_positions,
            )
        )
        await session.commit()
