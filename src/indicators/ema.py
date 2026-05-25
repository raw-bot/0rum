"""Exponential moving average helper shared across strategies and regime logic."""

import math


def ema(values: list[float], period: int) -> list[float]:
    """Return an EMA series seeded with the SMA of the first `period` values."""
    if period <= 0:
        raise ValueError("period must be positive")

    n = len(values)
    result = [math.nan] * n
    if n < period:
        return result

    alpha = 2.0 / (period + 1)
    seed = sum(float(v) for v in values[:period]) / period
    result[period - 1] = seed

    for i in range(period, n):
        result[i] = alpha * float(values[i]) + (1.0 - alpha) * result[i - 1]

    return result
