"""Unit tests for src/execution/signal_sender.py — SignalSender.

All tests use MagicMock + AsyncMock for the telegram.Bot instance.
No live Telegram calls are made. Each test is independent.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    StrategyName,
    Timeframe,
)


def make_signal(direction: str = "BUY") -> CandidateSignal:
    """Return a minimal CandidateSignal with realistic XAUUSD values."""
    dir_enum = Direction.BUY if direction == "BUY" else Direction.SELL
    # For SELL, adjust prices so sl > entry and tp < entry
    if direction == "BUY":
        return CandidateSignal(
            strategy=StrategyName.LIQUIDITY_SWEEP,
            direction=dir_enum,
            entry_price=2340.50,
            sl_price=2325.20,
            tp1_price=2358.80,
            tp2_price=2377.10,
            confidence=0.82,
            timeframe=Timeframe.M15,
            params_snapshot={"sweep_atr_mult": 0.5},
        )
    else:
        return CandidateSignal(
            strategy=StrategyName.LIQUIDITY_SWEEP,
            direction=dir_enum,
            entry_price=2340.50,
            sl_price=2355.80,
            tp1_price=2322.20,
            tp2_price=2303.90,
            confidence=0.82,
            timeframe=Timeframe.M15,
            params_snapshot={"sweep_atr_mult": 0.5},
        )


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
def sender(mock_bot):
    from src.execution.signal_sender import SignalSender
    return SignalSender(mock_bot)


@pytest.mark.asyncio
async def test_send_signal_buy_calls_bot(sender, mock_bot):
    """BUY signal calls bot.send_message exactly once."""
    signal = make_signal("BUY")
    await sender.send_signal(signal, Decimal("0.15"))
    mock_bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_signal_message_contains_required_fields(sender, mock_bot):
    """Message text contains all required signal fields."""
    signal = make_signal("BUY")
    await sender.send_signal(signal, Decimal("0.15"))

    call_kwargs = mock_bot.send_message.call_args.kwargs
    text = call_kwargs["text"]

    assert "BUY XAUUSD" in text
    assert "2340.50" in text
    assert "2325.20" in text
    assert "2358.80" in text
    assert "2377.10" in text
    assert "82%" in text or "0.82" in text  # confidence either way
    assert "0.15" in text


@pytest.mark.asyncio
async def test_send_signal_returns_true_on_success(sender, mock_bot):
    """Returns True when bot.send_message succeeds."""
    signal = make_signal("BUY")
    result = await sender.send_signal(signal, Decimal("0.15"))
    assert result is True


@pytest.mark.asyncio
async def test_send_signal_returns_false_on_telegram_error(mock_bot):
    """Returns False when bot.send_message raises an exception."""
    mock_bot.send_message = AsyncMock(side_effect=Exception("network error"))
    from src.execution.signal_sender import SignalSender
    sender = SignalSender(mock_bot)
    signal = make_signal("BUY")
    result = await sender.send_signal(signal, Decimal("0.15"))
    assert result is False


@pytest.mark.asyncio
async def test_send_signal_logs_send_failed_on_error(mock_bot):
    """Logs execution.send_failed event when bot.send_message raises."""
    mock_bot.send_message = AsyncMock(side_effect=Exception("timeout"))
    from src.execution.signal_sender import SignalSender

    logged_events = []

    class CapturingLogger:
        def error(self, event, **kwargs):
            logged_events.append(event)

        def info(self, event, **kwargs):
            pass

    sender = SignalSender(mock_bot)
    signal = make_signal("BUY")

    with patch("src.execution.signal_sender.log", CapturingLogger()):
        await sender.send_signal(signal, Decimal("0.15"))

    assert "execution.send_failed" in logged_events


@pytest.mark.asyncio
async def test_send_signal_sl_pct_negative_for_buy(sender, mock_bot):
    """For a BUY signal, SL percentage should be negative (SL is below entry)."""
    signal = make_signal("BUY")  # entry=2340.50, sl=2325.20 → sl_pct should be negative
    await sender.send_signal(signal, Decimal("0.15"))

    call_kwargs = mock_bot.send_message.call_args.kwargs
    text = call_kwargs["text"]

    # SL line should contain a negative percentage
    sl_line = [line for line in text.split("\n") if line.startswith("SL:")]
    assert sl_line, f"No SL line found in message: {text}"
    assert "-" in sl_line[0], f"SL percentage not negative for BUY: {sl_line[0]}"
