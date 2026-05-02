"""Tests for ExecutionRouter — SIGNAL/AUTO routing."""
import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_signal():
    from src.models.signal_data import CandidateSignal, Direction, StrategyName, Timeframe
    return CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=Direction.BUY,
        entry_price=Decimal("2340.50"),
        sl_price=Decimal("2325.20"),
        tp1_price=Decimal("2358.80"),
        tp2_price=Decimal("2377.10"),
        confidence=0.82,
        timeframe=Timeframe.M15,
        params_snapshot={},
    )


def make_mock_sender(return_value=True):
    sender = MagicMock()
    sender.send_signal = AsyncMock(return_value=return_value)
    return sender


@pytest.mark.asyncio
async def test_signal_mode_calls_send_signal():
    from src.execution.executor import ExecutionRouter
    sender = make_mock_sender(return_value=True)
    router = ExecutionRouter(signal_sender=sender)
    with patch("src.execution.executor.get_settings") as mock_settings:
        from src.config import ExecutionMode
        mock_settings.return_value.execution_mode = ExecutionMode.SIGNAL
        result = await router.execute(make_signal(), Decimal("0.10"))
    sender.send_signal.assert_called_once()
    assert result is True


@pytest.mark.asyncio
async def test_signal_mode_returns_false_on_failed_send():
    from src.execution.executor import ExecutionRouter
    sender = make_mock_sender(return_value=False)
    router = ExecutionRouter(signal_sender=sender)
    with patch("src.execution.executor.get_settings") as mock_settings:
        from src.config import ExecutionMode
        mock_settings.return_value.execution_mode = ExecutionMode.SIGNAL
        result = await router.execute(make_signal(), Decimal("0.10"))
    assert result is False


@pytest.mark.asyncio
async def test_auto_mode_raises_not_implemented():
    from src.execution.executor import ExecutionRouter
    sender = make_mock_sender()
    router = ExecutionRouter(signal_sender=sender)
    with patch("src.execution.executor.get_settings") as mock_settings:
        from src.config import ExecutionMode
        mock_settings.return_value.execution_mode = ExecutionMode.AUTO
        with pytest.raises(NotImplementedError):
            await router.execute(make_signal(), Decimal("0.10"))


@pytest.mark.asyncio
async def test_size_lots_passed_to_sender():
    from src.execution.executor import ExecutionRouter
    sender = make_mock_sender(return_value=True)
    router = ExecutionRouter(signal_sender=sender)
    size = Decimal("0.15")
    with patch("src.execution.executor.get_settings") as mock_settings:
        from src.config import ExecutionMode
        mock_settings.return_value.execution_mode = ExecutionMode.SIGNAL
        await router.execute(make_signal(), size)
    _, kwargs = sender.send_signal.call_args
    assert kwargs.get("size_lots") == size or sender.send_signal.call_args[0][1] == size
