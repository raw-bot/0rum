"""Indicator computations over an OHLCV candle buffer.

Every function returns the FULL series (same length as the input, with NaN
during warm-up) so the evaluator can read [-1] and [-2] honestly. Returning a
fabricated scalar is forbidden: a NaN that reaches the evaluator must surface
as an error, never be papered over (Fincept pitfalls #1 and #2).

Algorithms follow TA-Lib semantics and are tested against frozen TA-Lib
reference values (tests/fixtures/talib_reference.json); TA-Lib itself is not
a runtime dependency.

A candle is a dict with keys: ts, open, high, low, close, volume.
"""

from __future__ import annotations

import math

from orum.market_regime import rolling_return_regime

NAN = float("nan")

# Regime labels come from market_regime.rolling_return_regime. The DSL
# whitelists them so a typo in value_str is a schema rejection, not a
# condition that never fires.
REGIME_LABELS = ("favorable", "neutral", "unfavorable")
REGIME_LOOKBACK = 20


class IndicatorError(ValueError):
    """Raised for unknown indicators/fields; surfaced in EvalResult.errors."""


def _closes(candles: list[dict]) -> list[float]:
    return [float(candle["close"]) for candle in candles]


def sma(candles: list[dict], period: int) -> list[float]:
    closes = _closes(candles)
    out = [NAN] * len(closes)
    if len(closes) < period:
        return out
    window_sum = sum(closes[:period])
    out[period - 1] = window_sum / period
    for i in range(period, len(closes)):
        window_sum += closes[i] - closes[i - period]
        out[i] = window_sum / period
    return out


def ema(candles: list[dict], period: int) -> list[float]:
    # TA-Lib seeds the EMA with the SMA of the first `period` values; seeding
    # from index 0 or by summing NaN prefixes silently kills composed
    # indicators (Fincept pitfall #1).
    closes = _closes(candles)
    out = [NAN] * len(closes)
    if len(closes) < period:
        return out
    k = 2.0 / (period + 1.0)
    out[period - 1] = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        out[i] = (closes[i] - out[i - 1]) * k + out[i - 1]
    return out


def rsi(candles: list[dict], period: int) -> list[float]:
    # Wilder smoothing, TA-Lib convention: flat series -> RSI 0 (gain/(gain+loss)).
    closes = _closes(candles)
    out = [NAN] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period

    def _rsi_value(gain: float, loss: float) -> float:
        total = gain + loss
        return 100.0 * gain / total if total > 0.0 else 0.0

    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def atr(candles: list[dict], period: int) -> list[float]:
    out = [NAN] * len(candles)
    if len(candles) <= period:
        return out
    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        prev_close = float(candles[i - 1]["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    value = sum(true_ranges[:period]) / period
    out[period] = value
    for i in range(period + 1, len(candles)):
        value = (value * (period - 1) + true_ranges[i - 1]) / period
        out[i] = value
    return out


def bollinger(candles: list[dict], period: int, std_dev: float) -> dict[str, list[float]]:
    # TA-Lib BBANDS with matype=0 uses the population standard deviation.
    closes = _closes(candles)
    middle = sma(candles, period)
    upper = [NAN] * len(closes)
    lower = [NAN] * len(closes)
    pct_b = [NAN] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = middle[i]
        variance = sum((item - mean) ** 2 for item in window) / period
        deviation = math.sqrt(variance) * std_dev
        upper[i] = mean + deviation
        lower[i] = mean - deviation
        band_width = upper[i] - lower[i]
        if band_width > 0.0:
            pct_b[i] = (closes[i] - lower[i]) / band_width
    return {"upper": upper, "middle": middle, "lower": lower, "pct_b": pct_b}


def close(candles: list[dict]) -> list[float]:
    return _closes(candles)


def regime(candles: list[dict]) -> list[str]:
    """Existing market_regime classifier exposed as a label series."""
    closes = _closes(candles)
    labels: list[str] = []
    for i in range(len(closes)):
        window = closes[max(0, i - REGIME_LOOKBACK + 1) : i + 1]
        labels.append(rolling_return_regime(window)["label"])
    return labels


def first_valid_index(indicator: str, params: dict | None) -> int:
    """Index of the first non-NaN value; used for warm-up validation."""
    params = params or {}
    period = int(params.get("period", 0) or 0)
    if indicator in ("sma", "ema", "bollinger"):
        return period - 1
    if indicator in ("rsi", "atr"):
        return period
    if indicator in ("close", "regime"):
        return 0
    raise IndicatorError(f"unknown indicator {indicator!r}")


def series(indicator: str, params: dict | None, field: str | None, candles: list[dict]) -> list:
    """Dispatch to the named indicator and return its full series."""
    params = params or {}
    if indicator == "rsi":
        return rsi(candles, int(params["period"]))
    if indicator == "sma":
        return sma(candles, int(params["period"]))
    if indicator == "ema":
        return ema(candles, int(params["period"]))
    if indicator == "atr":
        return atr(candles, int(params["period"]))
    if indicator == "bollinger":
        outputs = bollinger(candles, int(params["period"]), float(params["std_dev"]))
        # No default field: silently picking one would be a Fincept-style
        # silent fallback. Schema validation requires the field upfront.
        if field not in outputs:
            raise IndicatorError(f"bollinger requires field in {sorted(outputs)}, got {field!r}")
        return outputs[field]
    if indicator == "close":
        return close(candles)
    if indicator == "regime":
        return regime(candles)
    raise IndicatorError(f"unknown indicator {indicator!r}")
