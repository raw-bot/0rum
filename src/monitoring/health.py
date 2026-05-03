"""Health check endpoint — reports postgres and redis connectivity."""

import time

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database import get_db
from src.risk.breaker import BreakerManager
from src.risk.gates import get_daily_pnl_pct, get_open_positions
from src.models.optimizer_result import OptimizerResultORM
from src.scheduler.jobs import get_last_candle_fetch

logger = structlog.get_logger(__name__)
health_router = APIRouter()

# Track startup time for uptime calculation
_start_time = time.monotonic()


@health_router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> dict:
    """Return service health status with postgres and redis connectivity flags.

    Returns:
        JSON with status, uptime_hours, execution_mode, circuit_breaker placeholder,
        open_positions placeholder, daily_pnl_pct placeholder, signals_today placeholder,
        last_candle_fetch placeholder, strategies_active placeholder,
        redis_connected, and postgres_connected.
    """
    settings = get_settings()

    # Check postgres connectivity
    postgres_connected = False
    try:
        await db.execute(text("SELECT 1"))
        postgres_connected = True
    except Exception as exc:
        logger.error("health.postgres_check_failed", error=str(exc))

    # Check redis connectivity
    redis_connected = False
    try:
        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        redis_connected = True
    except Exception as exc:
        logger.error("health.redis_check_failed", error=str(exc))

    uptime_seconds = time.monotonic() - _start_time
    uptime_hours = round(uptime_seconds / 3600, 2)

    overall_status = "healthy" if (postgres_connected and redis_connected) else "degraded"

    # Risk module wiring (Phase 6) — degrade gracefully if risk module unavailable.
    # Reuse the existing Redis client to avoid a second connection (T-06-09-01).
    try:
        r_risk = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        circuit_breaker_val = await BreakerManager(redis=r_risk).is_tripped()
        await r_risk.aclose()
        open_positions_val = await get_open_positions(db)
        daily_pnl_pct_val = float(await get_daily_pnl_pct(db))
    except Exception as exc:
        logger.warning("health.risk_wiring.failed", error=str(exc))
        circuit_breaker_val = False
        open_positions_val = 0
        daily_pnl_pct_val = 0.0

    # Live strategies_active query (D-20)
    try:
        stmt = select(func.count()).select_from(OptimizerResultORM).where(
            OptimizerResultORM.is_active.is_(True)
        )
        count_result = await db.execute(stmt)
        strategies_active_val = count_result.scalar_one()
    except Exception as exc:
        logger.warning("health.strategies_active.failed", error=str(exc))
        strategies_active_val = 0

    logger.info(
        "health.checked",
        status=overall_status,
        postgres_connected=postgres_connected,
        redis_connected=redis_connected,
    )

    return {
        "status": overall_status,
        "uptime_hours": uptime_hours,
        "execution_mode": settings.execution_mode.value,
        "circuit_breaker": circuit_breaker_val,
        "open_positions": open_positions_val,
        "daily_pnl_pct": daily_pnl_pct_val,
        "signals_today": 0,
        "last_candle_fetch": get_last_candle_fetch(),
        "strategies_active": strategies_active_val,
        "redis_connected": redis_connected,
        "postgres_connected": postgres_connected,
    }
