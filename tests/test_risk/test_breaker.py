"""Tests for src/risk/breaker.py — Redis state machine for the circuit breaker (RISK-05).

Uses fakeredis.FakeAsyncRedis via the function-scoped `fake_redis` and `breaker` fixtures
from conftest.py (Pitfall 8: NEVER session-scope fakeredis). Manual cooldown expiry uses
`await fake_redis.set(CB_COOLDOWN_UNTIL, past_iso_string)` to avoid time.sleep.
"""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.risk.breaker import BreakerManager, CB_COOLDOWN_UNTIL, CB_COUNTER, CB_TRIPPED_AT
from src.risk.events import CircuitBreakerAlert


@pytest.mark.asyncio
async def test_breaker_starts_untripped(breaker):
    assert await breaker.is_tripped() is False


@pytest.mark.asyncio
async def test_breaker_trips_on_nth_consecutive_stop(breaker, fake_redis):
    strategy = "liquidity_sweep"
    # First 7 stops must return None and leave breaker untripped.
    for _ in range(7):
        result = await breaker.record_stop(trade_id=uuid4(), strategy=strategy)
        assert result is None
    assert await breaker.is_tripped() is False

    # 8th stop trips the breaker.
    alert = await breaker.record_stop(trade_id=uuid4(), strategy=strategy)
    assert isinstance(alert, CircuitBreakerAlert)
    assert alert.consecutive_stops == 8
    assert alert.last_stop_strategy == strategy
    assert alert.last_stop_trade_id is not None
    assert await breaker.is_tripped() is True
    assert await fake_redis.get(CB_TRIPPED_AT) is not None


@pytest.mark.asyncio
async def test_breaker_resets_on_win(breaker):
    # Record 3 stops, then a win, then 1 more stop — should not trip.
    for _ in range(3):
        await breaker.record_stop(trade_id=uuid4(), strategy="trend_continuation")
    await breaker.record_win()
    result = await breaker.record_stop(trade_id=uuid4(), strategy="trend_continuation")
    assert result is None  # counter restarted from 0 → 1, far from threshold 8


@pytest.mark.asyncio
async def test_breaker_resets_after_cooldown(breaker, fake_redis):
    # Trip the breaker.
    for _ in range(8):
        await breaker.record_stop(trade_id=uuid4(), strategy="ema_momentum")
    assert await breaker.is_tripped() is True

    # Manually expire the cooldown.
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await fake_redis.set(CB_COOLDOWN_UNTIL, past)

    result = await breaker.reset_if_expired()
    assert result is True
    assert await breaker.is_tripped() is False
    assert int(await fake_redis.get(CB_COUNTER) or 0) == 0


@pytest.mark.asyncio
async def test_breaker_cooldown_ttl(breaker, fake_redis):
    # Trip the breaker; verify the TTL is ~86400 seconds (24 hours).
    for _ in range(8):
        await breaker.record_stop(trade_id=uuid4(), strategy="breakout_expansion")
    ttl = await fake_redis.ttl(CB_COOLDOWN_UNTIL)
    assert 86398 <= ttl <= 86400


@pytest.mark.asyncio
async def test_reset_if_expired_idempotent_when_not_tripped(breaker):
    assert await breaker.reset_if_expired() is False
    assert await breaker.reset_if_expired() is False


@pytest.mark.asyncio
async def test_alert_payload_fields(breaker, fake_redis):
    tid = uuid4()
    for _ in range(7):
        await breaker.record_stop(uuid4(), "trend_continuation")
    alert = await breaker.record_stop(tid, "ema_momentum")

    assert alert is not None
    assert alert.consecutive_stops == 8
    assert alert.last_stop_strategy == "ema_momentum"
    assert alert.last_stop_trade_id == tid
    assert isinstance(alert.tripped_at, datetime)
    assert alert.cooldown_until > alert.tripped_at


@pytest.mark.asyncio
async def test_no_trip_below_threshold(breaker):
    for _ in range(7):
        result = await breaker.record_stop(trade_id=uuid4(), strategy="liquidity_sweep")
        assert result is None
    assert await breaker.is_tripped() is False
