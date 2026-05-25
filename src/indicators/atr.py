"""Average True Range helpers shared by strategies and backtests."""


def true_ranges(candles: list) -> list[float]:
    """Return pairwise true ranges for candles ordered oldest to newest."""
    ranges: list[float] = []
    for i in range(1, len(candles)):
        high = float(candles[i].high)
        low = float(candles[i].low)
        prev_close = float(candles[i - 1].close)
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return ranges


def atr_wilder(candles: list, period: int = 14) -> float:
    """Compute ATR with Wilder smoothing and a SMA seed."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not candles:
        return 0.0
    if len(candles) == 1:
        return float(candles[0].high) - float(candles[0].low)

    ranges = true_ranges(candles)
    if len(ranges) < period:
        return sum(ranges) / len(ranges)

    atr = sum(ranges[:period]) / period
    alpha = 1.0 / period
    for value in ranges[period:]:
        atr = alpha * value + (1.0 - alpha) * atr
    return float(atr)


def atr_sma(candles: list, period: int = 14) -> float:
    """Compute diagnostic ATR as a simple mean of the last `period` true ranges."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(candles) < period + 1:
        return 0.0

    ranges = true_ranges(candles)
    return float(sum(ranges[-period:]) / period)
