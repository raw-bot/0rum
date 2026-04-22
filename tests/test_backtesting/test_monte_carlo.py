"""Unit tests for src/backtesting/monte_carlo.py."""

import numpy as np
import pytest

from src.backtesting.monte_carlo import monte_carlo_passes, run_monte_carlo


# ---------------------------------------------------------------------------
# run_monte_carlo
# ---------------------------------------------------------------------------


def test_run_monte_carlo_returns_required_keys():
    np.random.seed(42)
    pnl = np.array([1.0, 2.0, -0.5, 3.0, -1.0])
    result = run_monte_carlo(pnl, n_simulations=100)
    assert set(result.keys()) == {"historical_max_drawdown", "p95_drawdown", "p5_profit_factor"}


def test_p95_dd_gate_passes_profitable_sequence():
    np.random.seed(42)
    # Mixed but strongly profitable: 4 large winners vs 1 small loser repeated
    # Any bootstrap resample will have winners >> losers → pf > 1.0 reliably
    pnl = np.array([10.0, 10.0, 10.0, 10.0, -1.0] * 10)  # 50 elements, IS pf = 400/10 = 40
    result = run_monte_carlo(pnl, n_simulations=100)
    assert result["p5_profit_factor"] > 1.0


def test_p5_pf_gate_fails_losing_sequence():
    np.random.seed(42)
    # All losses
    pnl = np.array([-5.0, -3.0, -4.0, -6.0, -2.0])
    result = run_monte_carlo(pnl, n_simulations=100)
    assert result["p5_profit_factor"] == 0.0


def test_historical_drawdown_computed_from_original():
    np.random.seed(42)
    # Steady decline: cumsum will have a clear drawdown
    pnl = np.array([-1.0, -1.0, -1.0, -1.0, -1.0])
    result = run_monte_carlo(pnl, n_simulations=100)
    # historical_max_drawdown should be the maximum drawdown of cumsum([-1,-2,-3,-4,-5])
    # cumsum = [-1,-2,-3,-4,-5], running_max = [-1,-1,-1,-1,-1] (all negative, running max stays at 0
    # but np.maximum.accumulate starts from the first element)
    # running_max = [-1,-1,-1,-1,-1], drawdown = running_max - equity = [-1-(-1), -1-(-2), ...] = [0,1,2,3,4]
    assert result["historical_max_drawdown"] == pytest.approx(4.0)


def test_run_monte_carlo_all_values_are_floats():
    np.random.seed(42)
    pnl = np.array([1.0, -0.5, 2.0])
    result = run_monte_carlo(pnl, n_simulations=50)
    for v in result.values():
        assert isinstance(v, float)


# ---------------------------------------------------------------------------
# monte_carlo_passes
# ---------------------------------------------------------------------------


def test_monte_carlo_passes_both_gates_pass():
    results = {
        "historical_max_drawdown": 5.0,
        "p95_drawdown": 8.0,    # <= 2 * 5.0 = 10.0 ✓
        "p5_profit_factor": 1.5,  # > 1.0 ✓
    }
    assert monte_carlo_passes(results) is True


def test_monte_carlo_passes_dd_gate_fails():
    results = {
        "historical_max_drawdown": 5.0,
        "p95_drawdown": 11.0,   # > 2 * 5.0 = 10.0 ✗
        "p5_profit_factor": 1.5,
    }
    assert monte_carlo_passes(results) is False


def test_monte_carlo_passes_pf_gate_fails():
    results = {
        "historical_max_drawdown": 5.0,
        "p95_drawdown": 8.0,
        "p5_profit_factor": 0.9,  # <= 1.0 ✗
    }
    assert monte_carlo_passes(results) is False


def test_both_gates_required():
    # DD gate fails even when PF gate passes
    assert monte_carlo_passes({
        "historical_max_drawdown": 5.0,
        "p95_drawdown": 11.0,
        "p5_profit_factor": 1.5,
    }) is False
    # PF gate fails even when DD gate passes
    assert monte_carlo_passes({
        "historical_max_drawdown": 5.0,
        "p95_drawdown": 8.0,
        "p5_profit_factor": 0.5,
    }) is False
