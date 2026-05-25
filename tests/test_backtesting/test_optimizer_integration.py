"""Integration tests for WalkForwardOptimizer orchestration logic.

These tests verify the end-to-end orchestration of WalkForwardOptimizer.run()
using mocked DB-touching methods and patched computation. They do NOT require
a live PostgreSQL instance or real strategy execution.

Three paths tested:
1. Sufficient data + passing combo → _activate_best_params called for all strategies
2. Sufficient data + no passing combo (WFE gate fails) → _activate_best_params not called
3. Empty DB (no candles) → data guard fires, _activate_best_params not called, warning logged
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from src.backtesting.optimizer import SimulatedTradeResult, WalkForwardOptimizer
from src.backtesting.walk_forward import MIN_CANDLES_FOR_OPTIMIZER, WalkForwardWindow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sufficient_candles() -> dict[str, list]:
    """Return a candle dict with counts that satisfy all MIN_CANDLES_FOR_OPTIMIZER guards.

    Uses plain MagicMock objects (no real ORM) because _fetch_all_candles is
    patched — these objects are only used by _check_sufficient_data (len check)
    and by the timestamp max() call in run(). Each mock has a .timestamp set.
    """
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles: dict[str, list] = {}
    for tf, minimum in MIN_CANDLES_FOR_OPTIMIZER.items():
        mocks = [MagicMock() for _ in range(minimum + 10)]
        for m in mocks:
            m.timestamp = ts
            m.source_kind = "research"
            m.research_source = "dukascopy"
        candles[tf] = mocks
    return candles


def _fake_window() -> WalkForwardWindow:
    return WalkForwardWindow(
        window_num=3,
        train_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        train_end=datetime(2024, 7, 1, tzinfo=timezone.utc),
        oos_start=datetime(2024, 7, 1, tzinfo=timezone.utc),
        oos_end=datetime(2024, 9, 1, tzinfo=timezone.utc),
    )


def _passing_result() -> dict:
    """Pre-built result dict that satisfies all post-evaluate checks in run()."""
    window = _fake_window()
    trade_start = window.oos_start
    return {
        "combo": {"param_a": 0.5, "param_b": 0.7, "param_c": 2.0},
        "best_wfe": 0.65,
        "is_score": 1.8,
        "oos_score": 1.17,
        "oos_pf_per_window": [1.3, 1.1, 1.2],
        "trade_count": 42,
        "win_rate": 0.55,
        "max_dd": 150.0,
        "latest_window": window,
        "test_start": window.oos_start,
        "test_end": window.oos_end,
        # 40 trades — satisfies MIN_TRADES_FOR_EVALUATION (5) for Monte Carlo gate
        "all_oos_pnl": [10.0, -5.0, 15.0, -3.0, 8.0] * 8,
        "all_oos_trades": [
            SimulatedTradeResult(signal_timestamp=trade_start, pnl=10.0),
            SimulatedTradeResult(signal_timestamp=trade_start, pnl=-5.0),
            SimulatedTradeResult(signal_timestamp=trade_start, pnl=15.0),
            SimulatedTradeResult(signal_timestamp=trade_start, pnl=-3.0),
            SimulatedTradeResult(signal_timestamp=trade_start, pnl=8.0),
        ] * 8,
    }


# ---------------------------------------------------------------------------
# Test 1: Full activation path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimizer_full_run_activates_params() -> None:
    """End-to-end: sufficient data + passing combo → _activate_best_params called once per strategy.

    Patches:
    - _fetch_all_candles: returns sufficient candle mocks (bypasses DB)
    - _evaluate_all_combos: returns a pre-built passing result (bypasses LHS/backtest)
    - _activate_best_params: AsyncMock captures call count

    run() iterates over all 4 strategy classes. For each, _evaluate_all_combos
    returns the passing result, Monte Carlo is bypassed (patched), and
    _activate_best_params must be called exactly 4 times total.
    """
    optimizer = WalkForwardOptimizer()
    sufficient = _sufficient_candles()
    passing = _passing_result()

    # Patch run_monte_carlo and monte_carlo_passes to ensure MC gate always passes
    with (
        patch.object(
            optimizer, "_fetch_all_candles", new=AsyncMock(return_value=sufficient)
        ),
        patch.object(
            optimizer, "_evaluate_all_combos", new=AsyncMock(return_value=passing)
        ),
        patch.object(
            optimizer, "_activate_best_params", new=AsyncMock()
        ) as mock_activate,
        patch(
            "src.backtesting.optimizer.run_monte_carlo",
            return_value={
                "p95_drawdown": 100.0,
                "p5_profit_factor": 1.5,
                "historical_max_drawdown": 200.0,
            },
        ),
        patch(
            "src.backtesting.optimizer.monte_carlo_passes",
            return_value=True,
        ),
    ):
        await optimizer.run()

    # One call per strategy (4 strategies)
    assert mock_activate.call_count == 4


# ---------------------------------------------------------------------------
# Test 2: WFE gate — no activation when _evaluate_all_combos returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimizer_respects_wfe_gate() -> None:
    """WFE gate: when _evaluate_all_combos returns None, _activate_best_params is never called.

    Simulates the scenario where every parameter combo fails the WFE >= 0.50 gate
    or the multi-window gate. The strategy retains its previous active params
    (no deactivation call should occur).
    """
    optimizer = WalkForwardOptimizer()
    sufficient = _sufficient_candles()

    with (
        patch.object(
            optimizer, "_fetch_all_candles", new=AsyncMock(return_value=sufficient)
        ),
        patch.object(
            optimizer, "_evaluate_all_combos", new=AsyncMock(return_value=None)
        ),
        patch.object(
            optimizer, "_activate_best_params", new=AsyncMock()
        ) as mock_activate,
    ):
        await optimizer.run()

    assert mock_activate.call_count == 0


# ---------------------------------------------------------------------------
# Test 3: Data guard — empty DB triggers early return with warning log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimizer_data_guard_on_empty_db() -> None:
    """Data guard: empty candle lists → early return, no activation, warning logged.

    Does NOT patch _check_sufficient_data — the real function runs against
    the empty dict. Asserts that:
    1. _activate_best_params is never called.
    2. The "optimizer.skipped.insufficient_data" warning is emitted via structlog.
    """
    optimizer = WalkForwardOptimizer()
    empty_candles: dict[str, list] = {tf: [] for tf in ["M15", "H1", "H4", "D1"]}

    with (
        patch.object(
            optimizer, "_fetch_all_candles", new=AsyncMock(return_value=empty_candles)
        ),
        patch.object(
            optimizer, "_activate_best_params", new=AsyncMock()
        ) as mock_activate,
        structlog.testing.capture_logs() as captured,
    ):
        await optimizer.run()

    # No activation
    assert mock_activate.call_count == 0

    # Warning event emitted by run() when data guard fires
    warning_events = [
        entry for entry in captured if entry.get("event") == "optimizer.skipped.insufficient_data"
    ]
    assert len(warning_events) >= 1, (
        f"Expected 'optimizer.skipped.insufficient_data' in log output, got: {captured}"
    )


@pytest.mark.asyncio
async def test_optimizer_retains_previous_when_monte_carlo_gate_fails() -> None:
    """Passing WFE + multi-window combo is not activated when Monte Carlo fails."""
    optimizer = WalkForwardOptimizer()
    sufficient = _sufficient_candles()
    passing = _passing_result()

    with (
        patch.object(
            optimizer, "_fetch_all_candles", new=AsyncMock(return_value=sufficient)
        ),
        patch.object(
            optimizer, "_evaluate_all_combos", new=AsyncMock(return_value=passing)
        ),
        patch.object(
            optimizer, "_activate_best_params", new=AsyncMock()
        ) as mock_activate,
        patch(
            "src.backtesting.optimizer.run_monte_carlo",
            return_value={
                "p95_drawdown": 11.0,
                "p5_profit_factor": 0.75,
                "historical_max_drawdown": 5.0,
            },
        ),
        patch(
            "src.backtesting.optimizer.monte_carlo_passes",
            return_value=False,
        ),
    ):
        await optimizer.run()

    mock_activate.assert_not_called()
    assert optimizer.last_run_diagnostics
    strategy_diagnostics = {
        key: value
        for key, value in optimizer.last_run_diagnostics.items()
        if not key.startswith("_")
    }
    assert strategy_diagnostics
    for diagnostics in strategy_diagnostics.values():
        assert diagnostics["final_stage"] == "mc_gate_failed"
        assert diagnostics["monte_carlo"]["dd_gate_passed"] is False
        assert diagnostics["monte_carlo"]["pf_gate_passed"] is False
