"""Unit tests for monitor_trades trade lifecycle state machine (Phase 7, Plan 04).

Tests call _process_trade() directly to isolate logic from scheduler/DB overhead.
All DB sessions are mocked — no live DB or Redis required.
"""

import importlib
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _ensure_real_jobs_module():
    """Ensure src.scheduler.jobs is the real module, not a stub from test_health_risk.

    test_health_risk.py injects an empty ModuleType stub for src.scheduler.jobs
    (only if not already in sys.modules). If our module is collected after it,
    the stub will be cached. We must evict the stub and force a real import.
    """
    jobs_mod = sys.modules.get("src.scheduler.jobs")
    if jobs_mod is None or not hasattr(jobs_mod, "_process_trade"):
        # Evict stub and any parent package stub so importlib can load the real module
        for key in list(sys.modules.keys()):
            if key in ("src.scheduler.jobs", "src.scheduler"):
                del sys.modules[key]
        real = importlib.import_module("src.scheduler.jobs")
        sys.modules["src.scheduler.jobs"] = real


_ensure_real_jobs_module()


def make_trade(
    status="OPEN",
    direction="BUY",
    sl=2325.20,
    tp1=2358.80,
    tp2=2377.10,
    entry=2340.50,
    size_lots=0.20,
    equity_at_open=10000,
    trailing_stop_price=None,
):
    """Build a mock TradeORM for testing _process_trade."""
    trade = MagicMock()
    trade.id = uuid.uuid4()
    trade.status = status
    trade.direction = direction
    trade.entry_price = Decimal(str(entry))
    trade.sl_price = Decimal(str(sl))
    trade.tp1_price = Decimal(str(tp1))
    trade.tp2_price = Decimal(str(tp2))
    trade.size_lots = Decimal(str(size_lots))
    trade.equity_at_open = Decimal(str(equity_at_open)) if equity_at_open is not None else None
    trade.trailing_stop_price = (
        Decimal(str(trailing_stop_price)) if trailing_stop_price else None
    )
    trade.opened_at = datetime.now(timezone.utc)
    trade.expired_at = None
    return trade


def make_session_factory():
    """Return a mock AsyncSessionLocal context manager factory.

    Supports nested 'async with session.begin()' pattern used by _process_trade/_close_trade.
    The session.execute returns a result whose scalar_one_or_none() returns None
    (simulates empty strategy_stats for the win_rate recompute path).
    """
    # Build a mock result that returns None for scalar_one_or_none
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    # session.begin() must return an async context manager
    @asynccontextmanager
    async def _begin_cm():
        yield None

    mock_session.begin = MagicMock(side_effect=lambda: _begin_cm())

    @asynccontextmanager
    async def _session_cm():
        yield mock_session

    mock_factory = MagicMock(side_effect=lambda: _session_cm())
    return mock_factory


def make_session():
    """Return a mock AsyncSession for direct _process_trade tests."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    return mock_session


async def apply_breaker_effects(effects):
    """Apply returned breaker side effects for direct _process_trade tests."""
    from src.scheduler.jobs import _apply_breaker_side_effect

    for effect in effects:
        await _apply_breaker_side_effect(effect)


@pytest.mark.asyncio
async def test_open_trade_expires_before_touch_checks():
    """OPEN trade older than trade_expiry_hours closes as EXPIRED before SL/TP checks."""
    from src.scheduler.jobs import _process_trade

    trade = make_trade(
        status="OPEN",
        direction="BUY",
        sl=90,
        entry=100,
        tp1=110,
        size_lots=1,
        equity_at_open=10000,
    )
    candle_ts = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)
    trade.opened_at = candle_ts - timedelta(hours=73)
    trade.expired_at = None
    session = make_session()

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
        await _process_trade(
            session=session,
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("111.00"),
            candle_low=Decimal("89.00"),
            mark_price=Decimal("101.00"),
            atr_h1=Decimal("10.00"),
            candle_timestamp=candle_ts,
        )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "EXPIRED"
    assert trade.closed_at == candle_ts
    assert trade.expired_at == candle_ts
    assert trade.pnl == Decimal("50.00")
    assert trade.pnl_pct == Decimal("0.00500")
    mock_breaker.record_stop.assert_not_called()
    mock_breaker.record_win.assert_not_called()


@pytest.mark.asyncio
async def test_tp1_hit_trade_expires_with_partial_close_pnl():
    """TP1_HIT expiry preserves the TP1 half-close and marks the remaining half."""
    from src.scheduler.jobs import _process_trade

    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        entry=100,
        sl=90,
        tp1=112,
        tp2=130,
        size_lots=1,
        equity_at_open=10000,
    )
    trade.opened_at = datetime.now(timezone.utc) - timedelta(hours=73)
    trade.expired_at = None
    session = make_session()

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
        await _process_trade(
            session=session,
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("121.00"),
            candle_low=Decimal("119.00"),
            mark_price=Decimal("120.00"),
            atr_h1=Decimal("10.00"),
        )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "EXPIRED"
    assert trade.expired_at is not None
    assert trade.pnl == Decimal("1550.00")
    assert trade.pnl_pct == Decimal("0.15500")
    mock_breaker.record_stop.assert_not_called()
    mock_breaker.record_win.assert_not_called()


@pytest.mark.asyncio
async def test_process_trade_does_not_open_nested_session():
    """Direct processing mutates the trade with the passed session only."""
    from src.scheduler.jobs import _process_trade

    trade = make_trade(status="OPEN", direction="BUY", sl=90, entry=100, tp1=110)

    with patch("src.database.AsyncSessionLocal") as session_factory:
        await _process_trade(
            session=make_session(),
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("105.00"),
            candle_low=Decimal("95.00"),
            mark_price=Decimal("100.00"),
            atr_h1=Decimal("10.00"),
        )

    session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_open_buy_sl_touched_closes_as_sl():
    """OPEN BUY: candle_low <= sl_price → trade closes as SL."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2330.00"),
                candle_low=Decimal("2320.00"),  # below SL 2325.20
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "SL"


@pytest.mark.asyncio
async def test_open_buy_tp1_touched_transitions_to_tp1_hit():
    """OPEN BUY: candle_high >= tp1_price → status becomes TP1_HIT."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        from src.scheduler.jobs import _process_trade
        await _process_trade(
                session=make_session(),
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("2360.00"),  # above TP1 2358.80
            candle_low=Decimal("2345.00"),   # above SL 2325.20
            mark_price=Decimal("100.00"),
            atr_h1=Decimal("10.00"),
        )

    assert trade.status == "TP1_HIT"


@pytest.mark.asyncio
async def test_open_buy_both_touched_sl_wins():
    """OPEN BUY: both SL and TP1 in same candle → SL wins (D-05 conservative)."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2360.00"),  # above TP1
                candle_low=Decimal("2320.00"),   # below SL
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    # D-05: SL wins
    assert trade.status == "CLOSED"
    assert trade.close_reason == "SL"


@pytest.mark.asyncio
async def test_tp1_hit_trail_touched_closes_as_trail():
    """TP1_HIT: candle_low <= trailing_stop_price → closes as TRAIL."""
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        sl=2325.20,
        entry=2340.50,
        tp1=2358.80,
        tp2=2377.10,
        trailing_stop_price=2350.00,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2362.00"),  # doesn't reach TP2
                candle_low=Decimal("2348.00"),   # below trailing stop 2350.00
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "TRAIL"


@pytest.mark.asyncio
async def test_tp1_hit_tp2_touched_closes_as_tp2():
    """TP1_HIT: candle_high >= tp2_price (no trail touch) → closes as TP2.

    Setup: candle_high=2380, candle_low=2378, atr=1.0.
    Ratcheted trail = 2380 - 1 = 2379. candle_low=2378 <= 2379 → trail would be touched.

    To avoid trail: use candle_low=2378.50, atr=2. Ratchet = 2380 - 2 = 2378. candle_low=2378.50 > 2378 → no trail touch.
    TP2=2377.10 < candle_high=2380 → TP2 touched.
    """
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        sl=2325.20,
        entry=2340.50,
        tp1=2358.80,
        tp2=2377.10,
        trailing_stop_price=2370.00,  # initial trailing stop below candle range
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                # candle_high=2380, atr=2 → ratcheted trail = 2378
                # candle_low=2378.50 > 2378 → no trail touch
                # candle_high=2380 > tp2=2377.10 → TP2 touched
                candle_high=Decimal("2380.00"),
                candle_low=Decimal("2378.50"),
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("2.00"),
            )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "TP2"


@pytest.mark.asyncio
async def test_tp1_hit_both_touched_trail_wins():
    """TP1_HIT: both trail and TP2 touched in same candle → TRAIL wins (D-05)."""
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        sl=2325.20,
        entry=2340.50,
        tp1=2358.80,
        tp2=2377.10,
        trailing_stop_price=2350.00,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2380.00"),  # above TP2
                candle_low=Decimal("2348.00"),   # below trail stop 2350.00
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    # D-05: trailing stop wins on tie
    assert trade.status == "CLOSED"
    assert trade.close_reason == "TRAIL"


@pytest.mark.asyncio
async def test_trail_ratchets_only_upward_for_buy():
    """TP1_HIT BUY: trailing_stop_price only updates when new value is strictly better (higher)."""
    # Trade with trailing stop at 2350.00
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        sl=2325.20,
        entry=2340.50,
        tp1=2358.80,
        tp2=2400.00,  # TP2 very high — won't be hit
        trailing_stop_price=2350.00,
    )

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        from src.scheduler.jobs import _process_trade

        # First call: candle_high=2355.00, atr=10 → new_trail = 2355 - 10 = 2345.00 (worse, should NOT update)
        trade.trailing_stop_price = Decimal("2350.00")
        await _process_trade(
                session=make_session(),
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("2355.00"),  # 2355 - 10 = 2345 (worse than 2350)
            candle_low=Decimal("2353.00"),   # above trail 2350 — no trail touch
            mark_price=Decimal("100.00"),
            atr_h1=Decimal("10.00"),
        )
        # trailing_stop_price should remain 2350 (not updated to worse 2345)
        assert trade.trailing_stop_price == Decimal("2350.00")

        # Reset status for second call
        trade.status = "TP1_HIT"

        # Second call: candle_high=2363.00, atr=10 → new_trail = 2363 - 10 = 2353.00 (better)
        await _process_trade(
                session=make_session(),
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("2363.00"),  # 2363 - 10 = 2353 (better than 2350)
            candle_low=Decimal("2358.00"),   # above trail 2350 — no trail touch
            mark_price=Decimal("100.00"),
            atr_h1=Decimal("10.00"),
        )
        # trailing_stop_price should update to 2353
        assert trade.trailing_stop_price == Decimal("2353.00")


@pytest.mark.asyncio
async def test_sl_close_calls_record_stop():
    """SL close returns a breaker stop effect that is applied after DB commit."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2330.00"),
                candle_low=Decimal("2320.00"),  # below SL
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )
            await apply_breaker_effects(effects)

    mock_breaker.record_stop.assert_called_once()


@pytest.mark.asyncio
async def test_direct_sl_close_uses_full_loss_not_blended_tp1_gain():
    """OPEN trade that hits SL before TP1 records the full SL loss and does not reset breaker."""
    trade = make_trade(status="OPEN", direction="BUY", sl=90, entry=100, tp1=112)

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("101.00"),
                candle_low=Decimal("89.00"),
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )
            await apply_breaker_effects(effects)

    assert trade.status == "CLOSED"
    assert trade.close_reason == "SL"
    assert trade.pnl == Decimal("-210.00")
    assert trade.pnl_pct == Decimal("-0.02100")
    mock_breaker.record_stop.assert_called_once()
    mock_breaker.record_win.assert_not_called()


@pytest.mark.asyncio
async def test_sl_close_records_account_return_not_price_return():
    """Closed trade pnl_pct is account return: USD P&L divided by equity_at_open."""
    trade = make_trade(
        status="OPEN",
        direction="BUY",
        entry=3300,
        sl=3295,
        tp1=3310,
        size_lots=0.20,
        equity_at_open=10000,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("3301.00"),
                candle_low=Decimal("3294.00"),
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "SL"
    assert trade.pnl == Decimal("-110.00")
    assert trade.pnl_pct == Decimal("-0.01100")


@pytest.mark.asyncio
async def test_sl_close_applies_instrument_spread_and_slippage_costs():
    """Live theoretical P&L must use the same default XAUUSD costs as backtests."""
    trade = make_trade(
        status="OPEN",
        direction="BUY",
        entry=3300,
        sl=3295,
        tp1=3310,
        size_lots=0.20,
        equity_at_open=10000,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("3301.00"),
                candle_low=Decimal("3294.00"),
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    assert trade.close_reason == "SL"
    assert trade.pnl == Decimal("-110.00")
    assert trade.pnl_pct == Decimal("-0.01100")


@pytest.mark.asyncio
async def test_sl_close_derives_account_return_from_quantized_usd_pnl():
    """Fractional-cent USD P&L is rounded before deriving account return."""
    trade = make_trade(
        status="OPEN",
        direction="BUY",
        entry=100,
        sl=Decimal("99.9498"),
        tp1=110,
        size_lots=0.20,
        equity_at_open=100,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("100.00"),
                candle_low=Decimal("99.94"),
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    assert trade.pnl == Decimal("-11.00")
    assert trade.pnl_pct == Decimal("-0.11000")


@pytest.mark.asyncio
async def test_blended_tp2_close_records_usd_and_account_return():
    """TP2 after TP1 blends the half-size TP1 leg with the half-size final leg."""
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        entry=100,
        sl=90,
        tp1=110,
        tp2=130,
        size_lots=0.20,
        equity_at_open=10000,
        trailing_stop_price=50,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("130.00"),
                candle_low=Decimal("120.00"),
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("100.00"),
            )

    assert trade.close_reason == "TP2"
    assert trade.pnl == Decimal("390.00")
    assert trade.pnl_pct == Decimal("0.03900")


@pytest.mark.asyncio
async def test_sell_sl_close_records_usd_and_account_return():
    """SELL SL close uses the inverse direction sign for USD/account P&L."""
    trade = make_trade(
        status="OPEN",
        direction="SELL",
        entry=3300,
        sl=3305,
        tp1=3290,
        size_lots=0.20,
        equity_at_open=10000,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("3306.00"),
                candle_low=Decimal("3299.00"),
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("10.00"),
            )

    assert trade.close_reason == "SL"
    assert trade.pnl == Decimal("-110.00")
    assert trade.pnl_pct == Decimal("-0.01100")


@pytest.mark.asyncio
async def test_monitor_trades_ignores_candle_before_trade_opened():
    """A trade opened after the latest completed M15 candle cannot be closed by that candle."""
    from src.scheduler.jobs import monitor_trades

    candle = MagicMock()
    candle.high = Decimal("2360.00")
    candle.low = Decimal("2320.00")
    candle.close = Decimal("2340.00")
    candle.timestamp = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)

    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)
    trade.opened_at = datetime(2026, 5, 6, 10, 5, tzinfo=timezone.utc)

    m15_result = MagicMock()
    m15_result.scalar_one_or_none.return_value = candle
    h1_result = MagicMock()
    h1_result.scalars.return_value.all.return_value = []
    trades_result = MagicMock()
    trades_result.all.return_value = [(trade, "liquidity_sweep")]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[m15_result, h1_result, trades_result])

    @asynccontextmanager
    async def _begin_cm():
        yield None

    mock_session.begin = MagicMock(side_effect=lambda: _begin_cm())

    @asynccontextmanager
    async def _session_cm():
        yield mock_session

    session_factory = MagicMock(side_effect=lambda: _session_cm())

    with patch("src.database.AsyncSessionLocal", session_factory):
        with patch("src.scheduler.jobs._process_trade", new_callable=AsyncMock) as process_trade:
            await monitor_trades()

    process_trade.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_trades_applies_breaker_side_effects_after_transaction():
    """Breaker calls happen after the monitor DB transaction has committed."""
    from src.scheduler.jobs import monitor_trades

    tx_state = {"active": False}
    breaker_call_states = []

    candle = MagicMock()
    candle.high = Decimal("101.00")
    candle.low = Decimal("89.00")
    candle.close = Decimal("95.00")
    candle.timestamp = datetime.now(timezone.utc)

    trade = make_trade(status="OPEN", direction="BUY", sl=90, entry=100, tp1=110)
    trade.opened_at = datetime.now(timezone.utc) - timedelta(minutes=30)

    m15_result = MagicMock()
    m15_result.scalar_one_or_none.return_value = candle
    h1_result = MagicMock()
    h1_result.scalars.return_value.all.return_value = []
    trades_result = MagicMock()
    trades_result.all.return_value = [(trade, "liquidity_sweep")]
    stats_result = MagicMock()
    stats_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute = AsyncMock(
        side_effect=[
            m15_result,
            h1_result,
            trades_result,
            MagicMock(),
            stats_result,
        ]
    )

    @asynccontextmanager
    async def _begin_cm():
        tx_state["active"] = True
        try:
            yield None
        finally:
            tx_state["active"] = False

    mock_session.begin = MagicMock(side_effect=lambda: _begin_cm())

    @asynccontextmanager
    async def _session_cm():
        yield mock_session

    mock_breaker = AsyncMock()

    async def _record_stop(*args, **kwargs):
        breaker_call_states.append(tx_state["active"])

    mock_breaker.record_stop = AsyncMock(side_effect=_record_stop)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", MagicMock(side_effect=lambda: _session_cm())):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            with patch(
                "src.execution.paper.service.record_paper_account_snapshot",
                new_callable=AsyncMock,
            ):
                await monitor_trades()

    assert breaker_call_states == [False]


@pytest.mark.asyncio
async def test_win_close_calls_record_win():
    """Win close (pnl_pct > 0) must call BreakerManager.record_win() inline (D-08).

    Use TP2 close (same no-trail-touch setup as test_tp1_hit_tp2_touched_closes_as_tp2).
    """
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        sl=2325.20,
        entry=2340.50,
        tp1=2358.80,
        tp2=2377.10,
        trailing_stop_price=2370.00,
    )

    mock_breaker = AsyncMock()
    mock_breaker.record_stop = AsyncMock(return_value=None)
    mock_breaker.record_win = AsyncMock()

    with patch("src.database.AsyncSessionLocal", make_session_factory()):
        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            effects = await _process_trade(
                session=make_session(),
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2380.00"),  # above TP2 2377.10
                candle_low=Decimal("2378.50"),   # ratcheted trail = 2380-2=2378, 2378.50 > 2378 → no trail touch
                mark_price=Decimal("100.00"),
                atr_h1=Decimal("2.00"),
            )
            await apply_breaker_effects(effects)

    mock_breaker.record_win.assert_called_once()
