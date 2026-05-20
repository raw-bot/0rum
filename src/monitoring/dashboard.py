"""Dashboard API endpoint and HTML route — read-only operator dashboard.

Routes:
    GET /api/dashboard — JSON payload per UI-SPEC Data Source Contract.
    GET /dashboard     — Jinja2 HTML template (static shell; JS polls /api/dashboard).

No authentication (D-25 — operator tool, runs locally or behind firewall).
Read-only: no mutations on any endpoint.
"""

from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database import AsyncSessionLocal, get_db
from src.models.optimizer_result import OptimizerResultORM
from src.models.signal import ApprovedSignalORM, CandidateSignalORM
from src.models.strategy_stats import StrategyStatsORM
from src.models.trade import TradeORM
from src.risk.breaker import BreakerManager

log = structlog.get_logger(__name__)

dashboard_router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@dashboard_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    """Serve the operator dashboard HTML page.

    The template is a static shell that fetches /api/dashboard via JS.
    No server-side data injection — context is empty.
    """
    return templates.TemplateResponse(request=request, name="dashboard.html", context={})


@dashboard_router.get("/api/dashboard")
async def dashboard_api(db: AsyncSession = Depends(get_db)) -> dict:
    """Aggregate and return dashboard data JSON.

    Returns the exact shape specified in 07-UI-SPEC.md Data Source Contract.
    All errors degrade gracefully — sections that fail return empty/default values.
    """
    settings = get_settings()
    result: dict = {}

    # --- Health ---
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        log.warning("dashboard.db_check_failed", error=str(exc))

    redis_ok = False
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        redis_ok = True
    except Exception as exc:
        log.warning("dashboard.redis_check_failed", error=str(exc))

    strategies_active = 0
    try:
        stmt = select(func.count()).select_from(OptimizerResultORM).where(
            OptimizerResultORM.is_active.is_(True)
        )
        res = await db.execute(stmt)
        strategies_active = res.scalar_one()
    except Exception as exc:
        log.warning("dashboard.strategies_active_failed", error=str(exc))

    result["health"] = {"db": db_ok, "redis": redis_ok, "strategies_active": strategies_active}
    result["execution_mode"] = settings.execution_mode.value
    result["account"] = {
        "theoretical_equity_usd": float(settings.theoretical_equity_usd),
        "risk_per_trade": float(settings.risk_per_trade),
        "daily_loss_limit": float(settings.daily_loss_limit),
    }

    # --- Circuit Breaker ---
    cb_tripped = False
    consecutive_stops = 0
    try:
        r_cb = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        bm = BreakerManager(redis=r_cb)
        cb_tripped = await bm.is_tripped()
        consecutive_stops = await bm.get_consecutive_stops()
        await r_cb.aclose()
    except Exception as exc:
        log.warning("dashboard.circuit_breaker_failed", error=str(exc))
    result["circuit_breaker"] = {"tripped": cb_tripped, "consecutive_stops": consecutive_stops}

    # --- Daily P&L ---
    daily_pnl = 0.0
    try:
        today = datetime.now(timezone.utc).date()
        pnl_result = await db.execute(
            select(func.sum(TradeORM.pnl_pct)).where(
                TradeORM.closed_at.isnot(None),
                func.date(TradeORM.closed_at) == today,
            )
        )
        daily_pnl = float(pnl_result.scalar_one() or 0)
    except Exception as exc:
        log.warning("dashboard.daily_pnl_failed", error=str(exc))
    result["daily_pnl_pct"] = daily_pnl

    # --- Open Trades (status IN OPEN, TP1_HIT) with strategy name via JOIN ---
    open_trades = []
    try:
        trades_stmt = (
            select(TradeORM, CandidateSignalORM.strategy)
            .join(ApprovedSignalORM, TradeORM.approved_signal_id == ApprovedSignalORM.id)
            .join(
                CandidateSignalORM,
                ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id,
            )
            .where(TradeORM.status.in_(["OPEN", "TP1_HIT"]))
            .order_by(TradeORM.opened_at.desc())
        )
        trades_result = await db.execute(trades_stmt)
        for trade, strategy in trades_result.all():
            # Show trailing_stop_price as current SL for TP1_HIT trades
            current_sl = (
                float(trade.trailing_stop_price)
                if trade.status == "TP1_HIT" and trade.trailing_stop_price is not None
                else float(trade.sl_price)
            )
            open_trades.append({
                "id": str(trade.id),
                "direction": trade.direction,
                "strategy": strategy,
                "entry_price": float(trade.entry_price),
                "sl_price": current_sl,
                "tp1_price": float(trade.tp1_price),
                "size_lots": float(trade.size_lots),
                "status": trade.status,
                "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
            })
    except Exception as exc:
        log.warning("dashboard.open_trades_failed", error=str(exc))
    result["open_trades"] = open_trades

    # --- Latest Signals (last 20 ApprovedSignalORM rows) ---
    latest_signals = []
    try:
        sigs_stmt = (
            select(ApprovedSignalORM, CandidateSignalORM, TradeORM.size_lots)
            .join(
                CandidateSignalORM,
                ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id,
            )
            .outerjoin(
                TradeORM,
                TradeORM.approved_signal_id == ApprovedSignalORM.id,
            )
            .order_by(ApprovedSignalORM.created_at.desc())
            .limit(20)
        )
        sigs_result = await db.execute(sigs_stmt)
        for row in sigs_result.all():
            approved = row[0]
            cand = row[1]
            size_lots_raw = row[2]
            latest_signals.append({
                "id": str(approved.id),
                "strategy": cand.strategy,
                "direction": cand.direction,
                "entry_price": float(cand.entry_price),
                "sl_price": float(cand.sl_price),
                "tp1_price": float(cand.tp1_price),
                "tp2_price": float(cand.tp2_price) if cand.tp2_price is not None else None,
                "confidence": float(cand.confidence),
                "size_lots": float(size_lots_raw) if size_lots_raw is not None else None,
                "execution_status": approved.execution_status,
                "created_at": approved.created_at.isoformat() if approved.created_at else None,
            })
    except Exception as exc:
        log.warning("dashboard.latest_signals_failed", error=str(exc))
    result["latest_signals"] = latest_signals

    # --- Recently Closed Trades ---
    closed_trades = []
    try:
        closed_stmt = (
            select(TradeORM, CandidateSignalORM.strategy)
            .join(ApprovedSignalORM, TradeORM.approved_signal_id == ApprovedSignalORM.id)
            .join(
                CandidateSignalORM,
                ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id,
            )
            .where(TradeORM.status.in_(["CLOSED", "STOPPED"]))
            .order_by(TradeORM.closed_at.desc())
            .limit(20)
        )
        closed_result = await db.execute(closed_stmt)
        for trade, strategy in closed_result.all():
            closed_trades.append({
                "id": str(trade.id),
                "direction": trade.direction,
                "strategy": strategy,
                "entry_price": float(trade.entry_price),
                "close_reason": trade.close_reason,
                "pnl_pct": float(trade.pnl_pct) if trade.pnl_pct is not None else None,
                "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
            })
    except Exception as exc:
        log.warning("dashboard.closed_trades_failed", error=str(exc))
    result["closed_trades"] = closed_trades

    # --- Candidate Signal Decisions ---
    candidate_signals = []
    try:
        cand_stmt = (
            select(CandidateSignalORM)
            .order_by(CandidateSignalORM.created_at.desc())
            .limit(50)
        )
        cand_result = await db.execute(cand_stmt)
        for cand in cand_result.scalars().all():
            candidate_signals.append({
                "id": str(cand.id),
                "strategy": cand.strategy,
                "direction": cand.direction,
                "entry_price": float(cand.entry_price),
                "confidence": float(cand.confidence),
                "timeframe": cand.timeframe,
                "status": cand.status,
                "created_at": cand.created_at.isoformat() if cand.created_at else None,
            })
    except Exception as exc:
        log.warning("dashboard.candidate_signals_failed", error=str(exc))
    result["candidate_signals"] = candidate_signals

    # --- Operational Events ---
    operational_events = []
    if not db_ok:
        operational_events.append({
            "level": "warning",
            "event": "database_unreachable",
            "message": "PostgreSQL is unavailable; dashboard data is degraded.",
        })
    if not redis_ok:
        operational_events.append({
            "level": "warning",
            "event": "redis_unreachable",
            "message": "Redis is unavailable; circuit breaker state is degraded.",
        })
    result["operational_events"] = operational_events

    # --- Strategy Stats ---
    strategy_stats = []
    try:
        stats_stmt = select(StrategyStatsORM).order_by(StrategyStatsORM.win_rate.desc())
        stats_result = await db.execute(stats_stmt)
        for row in stats_result.scalars().all():
            strategy_stats.append({
                "strategy": row.strategy,
                "trade_count": row.trade_count,
                "wins": row.wins,
                "losses": row.losses,
                "win_rate": float(row.win_rate),
                "profit_factor": float(row.profit_factor),
                "total_pnl_pct": float(row.total_pnl_pct),
            })
    except Exception as exc:
        log.warning("dashboard.strategy_stats_failed", error=str(exc))
    result["strategy_stats"] = strategy_stats

    return result
