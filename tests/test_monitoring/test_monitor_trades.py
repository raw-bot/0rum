"""Unit tests for monitor_trades trade lifecycle state machine (Phase 7, Plan 04).

Tests call _process_trade() directly to isolate logic from scheduler/DB overhead.
All DB sessions are mocked — no live DB or Redis required.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_trade(
    status="OPEN",
    direction="BUY",
    sl=2325.20,
    tp1=2358.80,
    tp2=2377.10,
    entry=2340.50,
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
    trade.trailing_stop_price = (
        Decimal(str(trailing_stop_price)) if trailing_stop_price else None
    )
    return trade


@pytest.mark.asyncio
async def test_open_buy_sl_touched_closes_as_sl():
    """OPEN BUY: candle_low <= sl_price → trade closes as SL."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_session_factory.return_value = mock_ctx

        with patch("src.scheduler.jobs._breaker_manager") as mock_breaker:
            mock_breaker_instance = AsyncMock()
            mock_breaker_instance.record_stop = AsyncMock(return_value=None)
            mock_breaker_instance.record_win = AsyncMock()
            mock_breaker.__bool__ = lambda self: True
            mock_breaker.record_stop = AsyncMock(return_value=None)
            mock_breaker.record_win = AsyncMock()

            from src.scheduler.jobs import _process_trade
            await _process_trade(
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2330.00"),
                candle_low=Decimal("2320.00"),  # below SL 2325.20
                atr_h1=Decimal("10.00"),
            )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "SL"


@pytest.mark.asyncio
async def test_open_buy_tp1_touched_transitions_to_tp1_hit():
    """OPEN BUY: candle_high >= tp1_price → status becomes TP1_HIT."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session_factory.return_value = mock_ctx

        from src.scheduler.jobs import _process_trade
        await _process_trade(
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("2360.00"),  # above TP1 2358.80
            candle_low=Decimal("2345.00"),   # above SL 2325.20
            atr_h1=Decimal("10.00"),
        )

    assert trade.status == "TP1_HIT"


@pytest.mark.asyncio
async def test_open_buy_both_touched_sl_wins():
    """OPEN BUY: both SL and TP1 in same candle → SL wins (D-05 conservative)."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_session_factory.return_value = mock_ctx

        with patch("src.scheduler.jobs._breaker_manager") as mock_breaker:
            mock_breaker.record_stop = AsyncMock(return_value=None)
            mock_breaker.record_win = AsyncMock()

            from src.scheduler.jobs import _process_trade
            await _process_trade(
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2360.00"),  # above TP1
                candle_low=Decimal("2320.00"),   # below SL
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

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_session_factory.return_value = mock_ctx

        with patch("src.scheduler.jobs._breaker_manager") as mock_breaker:
            mock_breaker.record_stop = AsyncMock(return_value=None)
            mock_breaker.record_win = AsyncMock()

            from src.scheduler.jobs import _process_trade
            await _process_trade(
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2362.00"),  # doesn't reach TP2
                candle_low=Decimal("2348.00"),   # below trailing stop 2350.00
                atr_h1=Decimal("10.00"),
            )

    assert trade.status == "CLOSED"
    assert trade.close_reason == "TRAIL"


@pytest.mark.asyncio
async def test_tp1_hit_tp2_touched_closes_as_tp2():
    """TP1_HIT: candle_high >= tp2_price (no trail touch) → closes as TP2."""
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        sl=2325.20,
        entry=2340.50,
        tp1=2358.80,
        tp2=2377.10,
        trailing_stop_price=2352.00,  # trailing stop well below low
    )

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_session_factory.return_value = mock_ctx

        with patch("src.scheduler.jobs._breaker_manager") as mock_breaker:
            mock_breaker.record_stop = AsyncMock(return_value=None)
            mock_breaker.record_win = AsyncMock()

            from src.scheduler.jobs import _process_trade
            await _process_trade(
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2380.00"),  # above TP2 2377.10
                candle_low=Decimal("2360.00"),   # above trailing stop 2352 (no trail touch)
                atr_h1=Decimal("10.00"),
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

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_session_factory.return_value = mock_ctx

        with patch("src.scheduler.jobs._breaker_manager") as mock_breaker:
            mock_breaker.record_stop = AsyncMock(return_value=None)
            mock_breaker.record_win = AsyncMock()

            from src.scheduler.jobs import _process_trade
            await _process_trade(
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2380.00"),  # above TP2
                candle_low=Decimal("2348.00"),   # below trail stop 2350.00
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

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session_factory.return_value = mock_ctx

        from src.scheduler.jobs import _process_trade

        # First call: candle_high=2355.00 → new_trail = 2355 - 10 = 2345.00 (worse, should NOT update)
        trade.trailing_stop_price = Decimal("2350.00")
        await _process_trade(
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("2355.00"),  # 2355 - 10 = 2345 (worse)
            candle_low=Decimal("2353.00"),   # above trail 2350 — no trail touch
            atr_h1=Decimal("10.00"),
        )
        # trailing_stop_price should remain 2350 (not updated to worse 2345)
        assert trade.trailing_stop_price == Decimal("2350.00")

        # Reset status after no-change run (trade wasn't closed)
        trade.status = "TP1_HIT"

        # Second call: candle_high=2363.00 → new_trail = 2363 - 10 = 2353.00 (better)
        await _process_trade(
            trade=trade,
            strategy_name="liquidity_sweep",
            candle_high=Decimal("2363.00"),  # 2363 - 10 = 2353 (better than 2350)
            candle_low=Decimal("2358.00"),   # above trail 2350 — no trail touch
            atr_h1=Decimal("10.00"),
        )
        # trailing_stop_price should update to 2353
        assert trade.trailing_stop_price == Decimal("2353.00")


@pytest.mark.asyncio
async def test_sl_close_calls_record_stop():
    """SL close must call BreakerManager.record_stop() inline (D-07)."""
    trade = make_trade(status="OPEN", direction="BUY", sl=2325.20, entry=2340.50, tp1=2358.80)

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_session_factory.return_value = mock_ctx

        mock_breaker = AsyncMock()
        mock_breaker.record_stop = AsyncMock(return_value=None)
        mock_breaker.record_win = AsyncMock()

        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            await _process_trade(
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2330.00"),
                candle_low=Decimal("2320.00"),  # below SL
                atr_h1=Decimal("10.00"),
            )

    mock_breaker.record_stop.assert_called_once()


@pytest.mark.asyncio
async def test_win_close_calls_record_win():
    """Win close (pnl_pct > 0) must call BreakerManager.record_win() inline (D-08)."""
    # TP2 close gives blended pnl > 0 when both tp1 and exit are above entry
    trade = make_trade(
        status="TP1_HIT",
        direction="BUY",
        sl=2325.20,
        entry=2340.50,
        tp1=2358.80,
        tp2=2377.10,
        trailing_stop_price=2352.00,
    )

    with patch("src.scheduler.jobs.AsyncSessionLocal") as mock_session_factory:
        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=None)
        mock_begin.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin.return_value = mock_begin
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_session_factory.return_value = mock_ctx

        mock_breaker = AsyncMock()
        mock_breaker.record_stop = AsyncMock(return_value=None)
        mock_breaker.record_win = AsyncMock()

        with patch("src.scheduler.jobs._breaker_manager", mock_breaker):
            from src.scheduler.jobs import _process_trade
            await _process_trade(
                trade=trade,
                strategy_name="liquidity_sweep",
                candle_high=Decimal("2380.00"),  # above TP2 2377.10
                candle_low=Decimal("2360.00"),   # above trail 2352 → no trail touch
                atr_h1=Decimal("10.00"),
            )

    mock_breaker.record_win.assert_called_once()
