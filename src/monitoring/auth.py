"""Auth and rate limiting helpers for the local monitoring surface."""

import time

import redis.asyncio as aioredis
import structlog
from fastapi import Header, HTTPException, Request, status

from src.config import get_settings

log = structlog.get_logger(__name__)


async def require_dashboard_token(
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> None:
    """Require X-Dashboard-Token only when DASHBOARD_TOKEN is configured."""
    settings = get_settings()
    if settings.dashboard_token and x_dashboard_token != settings.dashboard_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="dashboard_token_required",
        )


async def rate_limit(request: Request) -> None:
    """Redis fixed-window limiter keyed by client IP and endpoint path.

    The local dashboard should remain observable during Redis incidents, so
    limiter infrastructure errors fail open while explicit limit breaches return
    HTTP 429.
    """
    settings = get_settings()
    limit = settings.dashboard_rate_limit_per_minute
    if limit <= 0:
        return

    client_ip = request.client.host if request.client else "unknown"
    endpoint = request.scope.get("path") or request.url.path
    window = int(time.time() // 60)
    key = f"monitoring:rate:{client_ip}:{endpoint}:{window}"
    redis_client = None

    try:
        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        count = int(await redis_client.incr(key))
        if count == 1:
            await redis_client.expire(key, 60)
        if count > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate_limit_exceeded",
            )
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("monitoring.rate_limit_failed_open", error=str(exc))
    finally:
        if redis_client is not None:
            await redis_client.aclose()
