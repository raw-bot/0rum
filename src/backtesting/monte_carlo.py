"""Monte Carlo bootstrap validation for walk-forward optimizer."""

from __future__ import annotations

import numpy as np
import structlog

log = structlog.get_logger(__name__)


def run_monte_carlo(
    daily_pnl: np.ndarray,
    n_simulations: int = 1000,
) -> dict[str, float]:
    """Run Monte Carlo bootstrap on the daily P&L vector from the OOS period.

    Resamples the daily_pnl array n_simulations times (with replacement).
    For each simulation, computes max drawdown from equity curve and profit
    factor. Returns P95 drawdown and P5 profit factor as robustness metrics.

    Args:
        daily_pnl: 1-D numpy float array of daily P&L values. Days with no
            trades should be included as 0.0 to maintain temporal density.
        n_simulations: Number of bootstrap simulations (1000 per CONTEXT.md).

    Returns:
        Dict with keys:
          "historical_max_drawdown": float — max drawdown from actual OOS sequence.
          "p95_drawdown": float — 95th percentile of simulated max drawdowns.
          "p5_profit_factor": float — 5th percentile of simulated profit factors.
    """
    n_days = len(daily_pnl)
    sim_max_drawdowns = np.zeros(n_simulations)
    sim_profit_factors = np.zeros(n_simulations)

    for i in range(n_simulations):
        resampled = np.random.choice(daily_pnl, size=n_days, replace=True)

        equity = np.cumsum(resampled)
        running_max = np.maximum.accumulate(equity)
        sim_max_drawdowns[i] = float((running_max - equity).max())

        winners = resampled[resampled > 0]
        losers = resampled[resampled < 0]
        if len(losers) > 0 and losers.sum() != 0:
            sim_profit_factors[i] = float(winners.sum() / abs(losers.sum()))
        else:
            sim_profit_factors[i] = 0.0

    equity_hist = np.cumsum(daily_pnl)
    running_max_hist = np.maximum.accumulate(equity_hist)
    hist_max_dd = float((running_max_hist - equity_hist).max())

    return {
        "historical_max_drawdown": hist_max_dd,
        "p95_drawdown": float(np.percentile(sim_max_drawdowns, 95)),
        "p5_profit_factor": float(np.percentile(sim_profit_factors, 5)),
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
