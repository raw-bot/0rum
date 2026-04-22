"""Unit tests for WalkForwardOptimizer — LHS sampling and data guard behaviour."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test-chat")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backtesting.optimizer import WalkForwardOptimizer

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

    from datetime import datetime, timezone
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
