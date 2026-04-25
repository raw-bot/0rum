"""Monte Carlo bootstrap validation for walk-forward optimizer."""

from __future__ import annotations

import numpy as np
import structlog

log = structlog.get_logger(__name__)

MONTE_CARLO_SEED = 20260425


def _profit_factor(pnl: np.ndarray) -> float:
    """Return gross-profit/gross-loss PF for one resampled P&L vector."""
    winners = pnl[pnl > 0]
    losers = pnl[pnl < 0]
    gross_profit = float(winners.sum())
    gross_loss = float(abs(losers.sum()))

    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return float("inf")
    return 0.0


def _percentile(values: np.ndarray, percentile: float) -> float:
    """Compute percentile while preserving all-infinite profit-factor samples."""
    sorted_values = np.sort(values)
    rank = (len(sorted_values) - 1) * (percentile / 100.0)
    lower_idx = int(np.floor(rank))
    upper_idx = int(np.ceil(rank))
    lower = float(sorted_values[lower_idx])
    upper = float(sorted_values[upper_idx])

    if lower_idx == upper_idx:
        return lower
    if np.isposinf(lower) or np.isposinf(upper):
        return float("inf")

    weight = rank - lower_idx
    return lower + (upper - lower) * weight


def run_monte_carlo(
    daily_pnl: np.ndarray,
    n_simulations: int = 1000,
    seed: int | None = MONTE_CARLO_SEED,
) -> dict[str, float]:
    """Run Monte Carlo bootstrap on the daily P&L vector from the OOS period.

    Resamples the daily_pnl array n_simulations times (with replacement).
    For each simulation, computes max drawdown from equity curve and profit
    factor. Returns P95 drawdown and P5 profit factor as robustness metrics.

    Args:
        daily_pnl: 1-D numpy float array of daily P&L values. Days with no
            trades should be included as 0.0 to maintain temporal density.
        n_simulations: Number of bootstrap simulations (1000 per CONTEXT.md).
        seed: Seed for a local RNG. Defaults to a fixed project seed so
            optimizer runs are reproducible end-to-end.

    Returns:
        Dict with keys:
          "historical_max_drawdown": float — max drawdown from actual OOS sequence.
          "p95_drawdown": float — 95th percentile of simulated max drawdowns.
          "p5_profit_factor": float — 5th percentile of simulated profit factors.
    """
    n_days = len(daily_pnl)
    sim_max_drawdowns = np.zeros(n_simulations)
    sim_profit_factors = np.zeros(n_simulations)
    rng = np.random.default_rng(seed)

    for i in range(n_simulations):
        resampled = rng.choice(daily_pnl, size=n_days, replace=True)

        equity = np.cumsum(resampled)
        running_max = np.maximum.accumulate(equity)
        sim_max_drawdowns[i] = float((running_max - equity).max())
        sim_profit_factors[i] = _profit_factor(resampled)

    equity_hist = np.cumsum(daily_pnl)
    running_max_hist = np.maximum.accumulate(equity_hist)
    hist_max_dd = float((running_max_hist - equity_hist).max())

    return {
        "historical_max_drawdown": hist_max_dd,
        "p95_drawdown": _percentile(sim_max_drawdowns, 95),
        "p5_profit_factor": _percentile(sim_profit_factors, 5),
    }


def monte_carlo_passes(results: dict[str, float]) -> bool:
    """Check OPTIM-05 gates on Monte Carlo results.

    Both gates must pass:
    1. P95 simulated drawdown <= 2 * historical max drawdown.
    2. P5 simulated profit factor > 1.0.

    Args:
        results: Output dict from run_monte_carlo().

    Returns:
        True only if BOTH gates pass.
    """
    dd_gate = results["p95_drawdown"] <= 2.0 * results["historical_max_drawdown"]
    pf_gate = results["p5_profit_factor"] > 1.0
    return dd_gate and pf_gate
