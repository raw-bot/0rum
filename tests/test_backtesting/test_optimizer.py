"""Unit tests for WalkForwardOptimizer — LHS sampling and data guard behaviour."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import DateTime

from src.backtesting.optimizer import (
    SimulatedTradeResult,
    WalkForwardOptimizer,
    _build_daily_pnl_series,
    _compute_aggregate_scores,
    _strategy_lhs_seed,
)
from src.backtesting.monte_carlo import run_monte_carlo
from src.models.optimizer_result import OptimizerResultORM

SAMPLE_PARAM_RANGES: dict[str, tuple[float, float]] = {
    "sweep_atr_mult": (0.2, 0.8),
    "sl_atr_mult": (0.3, 1.0),
    "tp_risk_mult": (1.2, 3.0),
}


# ---------------------------------------------------------------------------
# LHS sampling
# ---------------------------------------------------------------------------


def test_lhs_produces_100_combos():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=100)
    assert len(combos) == 100


def test_lhs_keys_match_param_ranges():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10)
    for combo in combos:
        assert set(combo.keys()) == set(SAMPLE_PARAM_RANGES.keys())


def test_lhs_values_in_range():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=100)
    for combo in combos:
        for name, value in combo.items():
            lo, hi = SAMPLE_PARAM_RANGES[name]
            assert lo <= value <= hi, f"{name}={value} outside [{lo}, {hi}]"


def test_lhs_values_are_python_floats():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10)
    for combo in combos:
        for value in combo.values():
            assert isinstance(value, float), f"Expected float, got {type(value)}"


def test_lhs_is_deterministic_when_seeded():
    opt = WalkForwardOptimizer()
    seed = 12345
    combos_a = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10, seed=seed)
    combos_b = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10, seed=seed)
    assert combos_a == combos_b


def test_strategy_lhs_seed_is_stable_and_distinct():
    liquidity_seed = _strategy_lhs_seed("liquidity_sweep")
    trend_seed = _strategy_lhs_seed("trend_continuation")
    assert liquidity_seed == _strategy_lhs_seed("liquidity_sweep")
    assert liquidity_seed != trend_seed


def test_optimizer_result_datetime_columns_are_timezone_aware():
    """Optimizer result timestamps must match TIMESTAMPTZ schema in 0001 migration."""
    for column_name in ["train_start", "train_end", "test_start", "test_end", "created_at"]:
        column = OptimizerResultORM.__table__.c[column_name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True, f"{column_name} must be timezone-aware"


def test_optimizer_result_max_drawdown_column_has_expanded_precision():
    column = OptimizerResultORM.__table__.c["max_drawdown"]
    assert column.type.precision == 12
    assert column.type.scale == 5


def test_compute_aggregate_scores_uses_all_windows():
    """Aggregate WFE must be derived from all IS/OOS trades, not just the last window."""
    is_pf, oos_pf, wfe = _compute_aggregate_scores(
        all_is_pnl=[10.0, 10.0, -5.0, -5.0],
        all_oos_pnl=[8.0, 8.0, -4.0, -4.0],
    )
    assert is_pf == pytest.approx(2.0)
    assert oos_pf == pytest.approx(2.0)
    assert wfe == pytest.approx(1.0)


def test_build_daily_pnl_series_sums_same_day_and_fills_gaps():
    """Monte Carlo input must be a dense daily vector with zero-filled missing days."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, tzinfo=timezone.utc)
    series = _build_daily_pnl_series(
        trade_results=[
            SimulatedTradeResult(signal_timestamp=start + timedelta(hours=1), pnl=10.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(hours=5), pnl=-3.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=2, hours=2), pnl=7.5),
        ],
        start=start,
        end=end,
    )
    assert series.tolist() == [7.0, 0.0, 7.5, 0.0]


def test_sparse_daily_monte_carlo_pipeline_is_deterministic_and_loss_sensitive():
    """Sparse OOS trades become dense daily PnL without making PF artificial."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=30)
    daily_pnl = _build_daily_pnl_series(
        trade_results=[
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=2), pnl=4.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=10), pnl=-10.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=20), pnl=3.0),
        ],
        start=start,
        end=end,
    )

    first = run_monte_carlo(daily_pnl, n_simulations=500, seed=77)
    second = run_monte_carlo(daily_pnl, n_simulations=500, seed=77)

    assert len(daily_pnl) == 30
    assert np.count_nonzero(daily_pnl) == 3
    assert first == second
    assert first["p5_profit_factor"] == 0.0
    assert first["historical_max_drawdown"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Data guard — run() must return early when insufficient data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_guard_skips_when_insufficient():
    """run() returns immediately without any DB writes when data is insufficient."""
    opt = WalkForwardOptimizer()

    # Sparse candles — well below minimums
    sparse = {tf: [] for tf in ["M15", "H1", "H4", "D1"]}

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=sparse)),
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as mock_activate,
    ):
        await opt.run()

    mock_activate.assert_not_called()


# ---------------------------------------------------------------------------
# Retain-previous behaviour — no deactivation when no combo passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retains_previous_on_no_passing_combo():
    """When _evaluate_all_combos returns None, _activate_best_params is not called."""
    opt = WalkForwardOptimizer()

    from src.backtesting.walk_forward import WalkForwardWindow, MIN_CANDLES_FOR_OPTIMIZER

    # Sufficient candle counts (just at minimum)
    sufficient = {
        "D1": [MagicMock()] * MIN_CANDLES_FOR_OPTIMIZER["D1"],
        "H4": [MagicMock()] * MIN_CANDLES_FOR_OPTIMIZER["H4"],
        "H1": [MagicMock()] * MIN_CANDLES_FOR_OPTIMIZER["H1"],
        "M15": [MagicMock()] * MIN_CANDLES_FOR_OPTIMIZER["M15"],
    }
    # Give each mock candle a timestamp so max() and window slicing work
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for tf_candles in sufficient.values():
        for c in tf_candles:
            c.timestamp = ts

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=sufficient)),
        patch.object(opt, "_evaluate_all_combos", new=AsyncMock(return_value=None)),
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as mock_activate,
    ):
        await opt.run()

    mock_activate.assert_not_called()
