"""Tests for src/risk/runner.py — RiskGateRunner orchestration.

Mocks the breaker, the three gate functions, and the sizer at their import path
inside src.risk.runner. Per D-12: when breaker is tripped, no gate or sizer is
called. Per D-15: RISK-01 rejection does NOT call breaker.record_stop.
"""

import pytest
import structlog
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    MarketRegime,
    MarketRegimeType,
    StrategyName,
    Timeframe,
)
from src.risk.events import CircuitBreakerAlert, PositionSizing, RiskDecision
from src.risk.runner import RiskGateRunner


def make_signal(
    direction=Direction.BUY,
    entry_price=2340.0,
    sl_price=2325.0,
) -> CandidateSignal:
    return CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=direction,
        entry_price=entry_price,
        sl_price=sl_price,
        tp1_price=entry_price + 2 * abs(entry_price - sl_price),
        confidence=0.75,
        timeframe=Timeframe.M15,
        params_snapshot={"sweep_atr_mult": 0.5},
    )


def make_regime(atr_pctile=0.5) -> MarketRegime:
    return MarketRegime(
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegimeType.TRENDING_UP,
        atr_value=15.0,
        atr_pctile=atr_pctile,
        adx_value=28.0,
    )


def _make_tripped_breaker():
    mock = MagicMock()
    mock.reset_if_expired = AsyncMock(return_value=False)
    mock.is_tripped = AsyncMock(return_value=True)
    mock.record_stop = AsyncMock(return_value=None)
    return mock


def _make_clear_breaker():
    mock = MagicMock()
    mock.reset_if_expired = AsyncMock(return_value=False)
    mock.is_tripped = AsyncMock(return_value=False)
    mock.record_stop = AsyncMock(return_value=None)
    return mock


def _normal_sizing(concentration_reduced=False) -> PositionSizing:
    return PositionSizing(
        risk_pct=0.01,
        risk_amount_usd=Decimal("100"),
        size_lots=Decimal("0.05"),
        vol_factor=1.0,
        concentration_reduced=concentration_reduced,
    )


@pytest.mark.asyncio
async def test_breaker_short_circuits_all_gates():
    """D-12: when breaker is tripped, none of the gates or sizer run."""
    mock_breaker = _make_tripped_breaker()
    with (
        patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock) as mock_dl,
        patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock) as mock_mp,
        patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock) as mock_cd,
        patch("src.risk.runner.calculate_position_size") as mock_sz,
    ):
        runner = RiskGateRunner(breaker=mock_breaker)
        result = await runner.evaluate(make_signal(), make_regime(), session=MagicMock())

    assert result.passed is False
    assert result.reason == "circuit_breaker_active"
    mock_dl.assert_not_called()
    mock_mp.assert_not_called()
    mock_cd.assert_not_called()
    mock_sz.assert_not_called()


@pytest.mark.asyncio
async def test_daily_loss_rejection_short_circuits_max_positions_and_sizer():
    mock_breaker = _make_clear_breaker()
    with (
        patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock, return_value=(False, -0.04)) as mock_dl,
        patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock) as mock_mp,
        patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock) as mock_cd,
        patch("src.risk.runner.calculate_position_size") as mock_sz,
    ):
        runner = RiskGateRunner(breaker=mock_breaker)
        result = await runner.evaluate(make_signal(), make_regime(), session=MagicMock())

    assert result.passed is False
    assert result.reason == "daily_loss_limit"
    mock_dl.assert_called_once()
    mock_mp.assert_not_called()
    mock_cd.assert_not_called()
    mock_sz.assert_not_called()


@pytest.mark.asyncio
async def test_daily_loss_does_not_trip_breaker():
    """D-15: RISK-01 rejection must never call breaker.record_stop."""
    mock_breaker = _make_clear_breaker()
    with (
        patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock, return_value=(False, -0.04)),
        patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock),
        patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock),
        patch("src.risk.runner.calculate_position_size"),
    ):
        runner = RiskGateRunner(breaker=mock_breaker)
        await runner.evaluate(make_signal(), make_regime(), session=MagicMock())

    mock_breaker.record_stop.assert_not_called()
    assert mock_breaker.record_stop.await_count == 0


@pytest.mark.asyncio
async def test_max_positions_rejection_short_circuits_sizer():
    mock_breaker = _make_clear_breaker()
    with (
        patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock, return_value=(True, 0.0)),
        patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock, return_value=(False, 5)),
        patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock) as mock_cd,
        patch("src.risk.runner.calculate_position_size") as mock_sz,
    ):
        runner = RiskGateRunner(breaker=mock_breaker)
        result = await runner.evaluate(make_signal(), make_regime(), session=MagicMock())

    assert result.passed is False
    assert result.reason == "max_positions"
    mock_cd.assert_not_called()
    mock_sz.assert_not_called()


@pytest.mark.asyncio
async def test_concentration_passes_with_reduced_size():
    """D-04: concentration is not a block — sets concentration_reduced=True on the decision."""
    sizing = _normal_sizing(concentration_reduced=True)
    mock_breaker = _make_clear_breaker()
    with (
        patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock, return_value=(True, 0.0)),
        patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock, return_value=(True, 2)),
        patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock, return_value=4),
        patch("src.risk.runner.calculate_position_size", return_value=sizing),
    ):
        runner = RiskGateRunner(breaker=mock_breaker)
        result = await runner.evaluate(make_signal(), make_regime(), session=MagicMock())

    assert result.passed is True
    assert result.concentration_reduced is True
    assert result.sizing is sizing


@pytest.mark.asyncio
async def test_all_pass_normal_path():
    sizing = _normal_sizing(concentration_reduced=False)
    mock_breaker = _make_clear_breaker()
    with (
        patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock, return_value=(True, 0.0)),
        patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock, return_value=(True, 1)),
        patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock, return_value=0),
        patch("src.risk.runner.calculate_position_size", return_value=sizing),
    ):
        runner = RiskGateRunner(breaker=mock_breaker)
        result = await runner.evaluate(make_signal(), make_regime(), session=MagicMock())

    assert result.passed is True
    assert result.reason is None
    assert result.sizing is not None
    assert result.concentration_reduced is False


@pytest.mark.asyncio
async def test_daily_loss_emits_structured_log():
    """Rejection must emit risk.gate.rejected with gate, reason, and daily_pnl_pct fields."""
    mock_breaker = _make_clear_breaker()
    with (
        patch("src.risk.runner.evaluate_daily_loss", new_callable=AsyncMock, return_value=(False, -0.04)),
        patch("src.risk.runner.evaluate_max_positions", new_callable=AsyncMock),
        patch("src.risk.runner.count_same_direction_open", new_callable=AsyncMock),
        patch("src.risk.runner.calculate_position_size"),
    ):
        runner = RiskGateRunner(breaker=mock_breaker)
        with structlog.testing.capture_logs() as logs:
            await runner.evaluate(make_signal(), make_regime(), session=MagicMock())

    rejection_logs = [l for l in logs if l.get("event") == "risk.gate.rejected"]
    assert len(rejection_logs) >= 1
    log_entry = rejection_logs[0]
    assert log_entry["gate"] == "daily_loss"
    assert log_entry["reason"] == "daily_loss_limit"
    assert "daily_pnl_pct" in log_entry


@pytest.mark.asyncio
async def test_alert_hook_invoked_on_trip(breaker, fake_redis):
    """Alert hook receives the CircuitBreakerAlert when the breaker trips.

    Exercises the contract: record_stop returns the alert, _publish_alert fans it out.
    This tests the hook surface directly without going through the runner evaluate path
    (Plan 07 note: Phase 7 wires the publish-on-trip; Phase 6 verifies the contract).
    """
    from src.risk.hooks import _publish_alert, register_alert_hook

    hook = AsyncMock()
    register_alert_hook(hook)

    # Trip via 8 direct record_stop calls.
    alert = None
    for _ in range(8):
        result = await breaker.record_stop(uuid4(), "liquidity_sweep")
        if result is not None:
            alert = result

    assert alert is not None
    await _publish_alert(alert)
    hook.assert_awaited_once_with(alert)
