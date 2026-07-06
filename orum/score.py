from __future__ import annotations

import math
from statistics import mean, pstdev

from orum.accounting import account_returns


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for item in returns:
        equity *= 1.0 + item
        peak = max(peak, equity)
        worst = min(worst, (equity - peak) / peak)
    return abs(worst)


def _clip(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def score(trades: list[dict], goal: dict) -> float:
    returns = account_returns(trades, goal)
    if not returns:
        return 0.0

    realised = math.prod(1.0 + item for item in returns) - 1.0
    drawdown = _max_drawdown(returns)
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = (mean(returns) / volatility * math.sqrt(len(returns))) if volatility else 0.0

    target = float(goal.get("target_return_30d", 0.07))
    max_drawdown = float(goal.get("max_drawdown", 0.05))
    min_sharpe = float(goal.get("min_sharpe", 1.3))

    return_component = _clip(realised / target)
    drawdown_component = _clip(1.0 - (drawdown / max_drawdown) * 2.0)
    sharpe_component = _clip(sharpe / min_sharpe)

    composite = (0.45 * return_component) + (0.35 * drawdown_component) + (0.20 * sharpe_component)
    if drawdown >= float(goal.get("emergency_stop_drawdown", 0.06)):
        return -1.0
    if drawdown >= max_drawdown:
        composite = min(composite, -0.6)
    if mean(returns) < 0 and realised < 0:
        composite = min(composite, max(return_component, sharpe_component, -0.05))
    return _clip(composite)
