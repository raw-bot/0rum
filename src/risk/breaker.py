"""Circuit breaker (RISK-05) — Redis-backed state machine.

State under prefix `risk:cb:` (per CONTEXT D-10): consecutive_stops counter,
tripped_at ISO timestamp, cooldown_until ISO timestamp with TTL.
Per AGENTS.md §12.3: counter resets on first winning trade OR cooldown expiry.
Per CONTEXT D-15: RISK-01 (daily loss) does NOT call record_stop — only
theoretical SL closes from Phase 7 do.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import redis.asyncio as aioredis
import structlog

from src.config import get_settings
from src.risk.events import CircuitBreakerAlert

log = structlog.get_logger(__name__)

CB_COUNTER = "risk:cb:consecutive_stops"
CB_TRIPPED_AT = "risk:cb:tripped_at"
CB_COOLDOWN_UNTIL = "risk:cb:cooldown_until"


class BreakerManager:
    def __init__(self, redis: aioredis.Redis | None = None):
        """Constructor-injected Redis client.

        decode_responses=True is REQUIRED — the breaker reads ISO-8601 strings.
        Without it, Redis returns bytes and datetime.fromisoformat raises TypeError.
        """
        settings = get_settings()
        self._redis = redis or aioredis.from_url(
            settings.redis_url, decode_responses=True
        )
        self._stops_threshold = settings.circuit_breaker_stops
        self._cooldown_seconds = settings.circuit_breaker_cooldown_hours * 3600
        self._kill_switch_key = settings.kill_switch_redis_key

    async def is_tripped(self) -> bool:
        cooldown = await self._redis.get(CB_COOLDOWN_UNTIL)
        if cooldown is None:
            return False
        return datetime.fromisoformat(cooldown) > datetime.now(timezone.utc)

    async def is_kill_switch_active(self) -> bool:
        value = await self._redis.get(self._kill_switch_key)
        if value is None:
            return False
        return str(value).strip().lower() not in {"", "0", "false", "off", "no"}

    async def activate_kill_switch(self) -> None:
        await self._redis.set(self._kill_switch_key, "1")
        log.warning("risk.kill_switch.activated")

    async def clear_kill_switch(self) -> None:
        await self._redis.delete(self._kill_switch_key)
        log.info("risk.kill_switch.cleared")

    async def reset_if_expired(self) -> bool:
        """Idempotent: returns False (no-op) when cooldown is absent or still in the future."""
        cooldown = await self._redis.get(CB_COOLDOWN_UNTIL)
        if cooldown is None:
            return False
        if datetime.fromisoformat(cooldown) <= datetime.now(timezone.utc):
            await self._redis.delete(CB_TRIPPED_AT, CB_COOLDOWN_UNTIL)
            await self._redis.set(CB_COUNTER, 0)
            log.info("risk.circuit_breaker.reset", reason="cooldown_expired")
            return True
        return False

    async def record_stop(
        self, trade_id: Optional[UUID], strategy: str
    ) -> Optional[CircuitBreakerAlert]:
        """Increment consecutive-stop counter; trip and emit Alert if threshold reached.

        Atomic INCR; cooldown_until written with redis TTL so keys self-expire.
        Returns the Alert ONLY on the trip event.
        """
        new_count = await self._redis.incr(CB_COUNTER)
        if new_count >= self._stops_threshold:
            tripped_at = datetime.now(timezone.utc)
            cooldown_until = tripped_at + timedelta(seconds=self._cooldown_seconds)
            await self._redis.set(CB_TRIPPED_AT, tripped_at.isoformat())
            await self._redis.set(
                CB_COOLDOWN_UNTIL,
                cooldown_until.isoformat(),
                ex=self._cooldown_seconds,
            )
            alert = CircuitBreakerAlert(
                tripped_at=tripped_at,
                consecutive_stops=new_count,
                cooldown_until=cooldown_until,
                last_stop_strategy=strategy,
                last_stop_trade_id=trade_id,
            )
            log.warning(
                "risk.circuit_breaker.tripped",
                consecutive_stops=new_count,
                cooldown_until=cooldown_until.isoformat(),
            )
            return alert
        return None

    async def record_win(self) -> None:
        await self._redis.set(CB_COUNTER, 0)
        log.info("risk.circuit_breaker.counter_reset", reason="winning_trade")

    async def get_consecutive_stops(self) -> int:
        """Return current consecutive stop count from Redis."""
        val = await self._redis.get(CB_COUNTER)
        return int(val) if val is not None else 0
