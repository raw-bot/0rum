"""Ex ante features journalled with every candidate signal.

These are computed ONLY from the candles available at decision time and are
logged for later attribution (E[R | feature]). Nothing in the baselines
branches on them — wiring a filter here would change the very signals the
baselines must reproduce (see the harness charter in __init__.py).
"""

from __future__ import annotations

import math

from orum.strategies.ha_trend import _ema, _rsi_last


def _atr(candles: list[dict], n: int = 14) -> float:
    """Wilder ATR — same recursion as orum.portfolio.paper_engine._atr."""
    if len(candles) < n + 1:
        return 0.0
    h = [c["high"] for c in candles]
    l = [c["low"] for c in candles]
    c = [c["close"] for c in candles]
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a = 1 / n
    atr = tr[0]
    for x in tr[1:]:
        atr = a * x + (1 - a) * atr
    return atr


def efficiency_ratio(closes: list[float], n: int = 10) -> float:
    """Kaufman ER: |net move| / sum(|bar moves|) over the last n bars."""
    if len(closes) < n + 1:
        return float("nan")
    direction = abs(closes[-1] - closes[-1 - n])
    volatility = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - n, len(closes)))
    return direction / volatility if volatility > 0 else 0.0


def rolling_volatility(closes: list[float], n: int = 20) -> float:
    """Stdev of log returns over the last n bars (per-bar, not annualized)."""
    if len(closes) < n + 1:
        return float("nan")
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - n, len(closes))]
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))


def candidate_features(candles: list[dict], *, stop: float, ema_len: int = 20,
                       slope_len: int = 5, touch_window: int = 5, rsi_len: int = 14) -> dict:
    """Everything about the setup, measured at the signal close."""
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    close = closes[-1]

    atr = _atr(candles)
    ema_high = _ema(highs, ema_len)
    ema_low = _ema(lows, ema_len)
    mid_now = (ema_high[-1] + ema_low[-1]) / 2.0
    mid_prev = (ema_high[-1 - slope_len] + ema_low[-1 - slope_len]) / 2.0

    # Trend age: consecutive bars (ending now) where both EMAs slope up.
    age = 0
    for i in range(len(candles) - 1, slope_len - 1, -1):
        if ema_high[i] > ema_high[i - slope_len] and ema_low[i] > ema_low[i - slope_len]:
            age += 1
        else:
            break

    # Pullback depth: how far the recent lows dug into the zone, in zone widths
    # (0 = only kissed the top EMA, 1 = reached the bottom EMA, >1 = broke it).
    zone_width = ema_high[-1] - ema_low[-1]
    recent_low = min(lows[-(touch_window + 1):])
    pullback_depth = (ema_high[-1] - recent_low) / zone_width if zone_width > 0 else float("nan")

    return {
        "atr_14": atr,
        "stop_distance": close - stop,
        "stop_distance_atr": (close - stop) / atr if atr > 0 else float("nan"),
        "channel_mid_slope_atr": (mid_now - mid_prev) / atr if atr > 0 else float("nan"),
        "efficiency_ratio_10": efficiency_ratio(closes, 10),
        "rolling_vol_20": rolling_volatility(closes, 20),
        "trend_age_bars": age,
        "pullback_depth": pullback_depth,
        "rsi_minus_50": _rsi_last(closes, rsi_len) - 50.0,
    }


def mae_mfe(monitor_candles: list[dict], *, entry_price: float, entry_ms: int,
            exit_ms: int) -> tuple[float, float]:
    """Max adverse / favourable excursion (long) between entry and exit, as
    fractions of the entry price, from the monitor-timeframe candles."""
    worst = best = 0.0
    for candle in monitor_candles:
        if candle["ts"] < entry_ms or candle["ts"] > exit_ms:
            continue
        worst = min(worst, (candle["low"] - entry_price) / entry_price)
        best = max(best, (candle["high"] - entry_price) / entry_price)
    return worst, best
