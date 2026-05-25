"""Risk account helpers for dynamic equity and aggregate exposure gates."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.paper.service import compute_current_paper_state
from src.market.instruments import get_instrument_spec
from src.models.paper import PaperAccountSnapshotORM
from src.models.trade import TradeORM

OPEN_RISK_STATUSES = ("OPEN", "TP1_HIT")


def _as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _utc_day_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _remaining_size_lots(trade: TradeORM) -> Decimal:
    size_lots = _as_decimal(trade.size_lots)
    if trade.status == "TP1_HIT":
        return size_lots * Decimal("0.5")
    return size_lots


def _stop_risk_distance(trade: TradeORM, stop_price: Decimal) -> Decimal:
    entry = _as_decimal(trade.entry_price)
    if trade.direction == "BUY":
        return max(entry - stop_price, Decimal("0"))
    return max(stop_price - entry, Decimal("0"))


async def get_current_equity(
    session: AsyncSession,
    starting_balance: Decimal,
) -> Decimal:
    """Return current paper equity for risk decisions."""

    state = await compute_current_paper_state(
        session,
        starting_balance=starting_balance,
    )
    return Decimal(str(state.equity))


async def get_daily_equity_pnl_pct(
    session: AsyncSession,
    fallback_equity: Decimal,
) -> Decimal:
    """Return current UTC-day realized P&L divided by equity at trade open."""

    stmt = (
        select(
            func.coalesce(func.sum(TradeORM.pnl), 0),
            func.coalesce(func.avg(TradeORM.equity_at_open), fallback_equity),
        )
        .where(
            TradeORM.closed_at >= _utc_day_start(),
            TradeORM.status == "CLOSED",
        )
    )
    result = await session.execute(stmt)
    total_pnl, equity_base = result.one()

    total_pnl = _as_decimal(total_pnl)
    equity_base = _as_decimal(equity_base or fallback_equity)
    if total_pnl == 0 or equity_base <= 0:
        return Decimal("0")
    return total_pnl / equity_base


async def get_open_exposure(session: AsyncSession) -> tuple[Decimal, Decimal]:
    """Return open notional and stop-risk USD for active paper trades."""

    stmt = select(TradeORM).where(TradeORM.status.in_(OPEN_RISK_STATUSES))
    result = await session.execute(stmt)
    trades = result.scalars().all()
    spec = get_instrument_spec("XAUUSD")

    notional_usd = Decimal("0")
    stop_risk_usd = Decimal("0")
    for trade in trades:
        entry = _as_decimal(trade.entry_price)
        remaining_size_lots = _remaining_size_lots(trade)

        if trade.status == "OPEN" and trade.notional_usd is not None:
            notional_usd += _as_decimal(trade.notional_usd)
        else:
            notional_usd += entry * remaining_size_lots * spec.contract_size

        if trade.status == "OPEN" and trade.risk_amount_usd is not None:
            stop_risk_usd += _as_decimal(trade.risk_amount_usd)
            continue

        stop_price = (
            _as_decimal(trade.trailing_stop_price)
            if trade.status == "TP1_HIT" and trade.trailing_stop_price is not None
            else _as_decimal(trade.sl_price)
        )
        stop_risk_usd += (
            _stop_risk_distance(trade, stop_price)
            * remaining_size_lots
            * spec.contract_size
        )

    return notional_usd, stop_risk_usd


async def get_max_drawdown_pct(
    session: AsyncSession,
    starting_balance: Decimal,
) -> Decimal:
    """Return non-negative peak-to-trough drawdown using snapshots/current equity."""

    starting_balance = Decimal(str(starting_balance))
    if starting_balance <= 0:
        return Decimal("0")

    result = await session.execute(
        select(PaperAccountSnapshotORM.equity).order_by(
            PaperAccountSnapshotORM.created_at.asc()
        )
    )
    equity_points = [_as_decimal(value) for value in result.scalars().all()]

    current_state = await compute_current_paper_state(
        session,
        starting_balance=starting_balance,
    )
    equity_points.append(_as_decimal(current_state.equity))

    if not equity_points:
        return Decimal("0")

    peak = starting_balance
    max_drawdown = Decimal("0")
    for equity in equity_points:
        if equity > peak:
            peak = equity
            continue
        if peak <= 0:
            continue
        drawdown = (peak - equity) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    return max_drawdown
