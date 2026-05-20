"""Tests for ExecutionRouter local execution routing."""

from decimal import Decimal
from unittest.mock import patch

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


@pytest.mark.asyncio
async def test_signal_mode_returns_true_without_external_sender():
    from src.config import ExecutionMode
    from src.execution.executor import ExecutionRouter

    router = ExecutionRouter()
    with patch("src.execution.executor.get_settings") as mock_settings:
        mock_settings.return_value.execution_mode = ExecutionMode.SIGNAL
        result = await router.execute(make_signal(), Decimal("0.10"))

    assert result is True


@pytest.mark.asyncio
async def test_auto_mode_raises_not_implemented():
    from src.config import ExecutionMode
    from src.execution.executor import ExecutionRouter

    router = ExecutionRouter()
    with patch("src.execution.executor.get_settings") as mock_settings:
        mock_settings.return_value.execution_mode = ExecutionMode.AUTO
        with pytest.raises(NotImplementedError):
            await router.execute(make_signal(), Decimal("0.10"))
