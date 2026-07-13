"""Closed-candle UT Bot trigger on M15 with an H1 EMA(200) entry filter.

The portfolio is long-only. A qualified UT Bot BUY opens this sleeve; the
independent slower SELL recurrence exits it. No short position is created.
"""

from __future__ import annotations

from dataclasses import dataclass

from orum.strategies.base import Side, Signal, StrategyContext


@dataclass(frozen=True)
class UtBotParams:
    buy_key: float = 6.0
    buy_atr_period: int = 10
    sell_key: float = 7.0
    sell_atr_period: int = 20
    trend_ema_period: int = 200


def _positive_float(config: dict, key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def _positive_int(config: dict, key: str, default: int) -> int:
    value = config.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def ema_last(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
    return current


def _wilder_atr_series(candles: list[dict], period: int) -> list[float | None]:
    if not candles:
        return []
    true_ranges: list[float] = []
    for index, candle in enumerate(candles):
        high = float(candle["high"])
        low = float(candle["low"])
        if index == 0:
            true_ranges.append(high - low)
        else:
            previous_close = float(candles[index - 1]["close"])
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

    out: list[float | None] = [None] * len(candles)
    if len(true_ranges) < period:
        return out
    atr = sum(true_ranges[:period]) / period
    out[period - 1] = atr
    for index in range(period, len(true_ranges)):
        atr = ((period - 1) * atr + true_ranges[index]) / period
        out[index] = atr
    return out


def utbot_signal_series(
    candles: list[dict], *, key_value: float, atr_period: int
) -> tuple[list[bool], list[bool], list[float | None]]:
    """Return causal BUY/SELL crossover flags and the ATR trailing stop."""
    closes = [float(candle["close"]) for candle in candles]
    atrs = _wilder_atr_series(candles, atr_period)
    stops: list[float | None] = [None] * len(candles)
    buys = [False] * len(candles)
    sells = [False] * len(candles)

    for index, atr in enumerate(atrs):
        if atr is None:
            continue
        loss = key_value * atr
        if index == 0 or stops[index - 1] is None:
            stops[index] = closes[index] - loss
            continue

        previous_stop = float(stops[index - 1])
        previous_close = closes[index - 1]
        close = closes[index]
        if close > previous_stop and previous_close > previous_stop:
            stop = max(previous_stop, close - loss)
        elif close < previous_stop and previous_close < previous_stop:
            stop = min(previous_stop, close + loss)
        elif close > previous_stop:
            stop = close - loss
        else:
            stop = close + loss
        stops[index] = stop
        buys[index] = previous_close <= previous_stop and close > stop
        sells[index] = previous_close >= previous_stop and close < stop

    return buys, sells, stops


class UtBotMtfEngine:
    name = "utbot_mtf"
    version = "1"
    required_timeframes = ["15m", "1h"]
    required_indicators = ["atr_trailing_stop", "ema200"]
    warmup_period = 201

    def __init__(self) -> None:
        self._params = UtBotParams()
        self._last_candle_ts: object | None = None

    def init(self, config: dict) -> None:
        cfg = config if isinstance(config, dict) else {}
        defaults = UtBotParams()
        self._params = UtBotParams(
            buy_key=_positive_float(cfg, "buy_key", defaults.buy_key),
            buy_atr_period=_positive_int(cfg, "buy_atr_period", defaults.buy_atr_period),
            sell_key=_positive_float(cfg, "sell_key", defaults.sell_key),
            sell_atr_period=_positive_int(cfg, "sell_atr_period", defaults.sell_atr_period),
            trend_ema_period=_positive_int(cfg, "trend_ema_period", defaults.trend_ema_period),
        )
        self.warmup_period = max(
            self._params.sell_atr_period + 2,
            self._params.trend_ema_period + 1,
        )
        self._last_candle_ts = None

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        candle_ts = candle.get("ts")
        if candle_ts == self._last_candle_ts:
            return None
        self._last_candle_ts = candle_ts

        m15 = context.candles_by_timeframe.get("15m", context.candles)
        h1 = context.candles_by_timeframe.get("1h", [])
        if len(m15) < self._params.sell_atr_period + 2 or len(h1) < self._params.trend_ema_period:
            return None

        buy, _, buy_stops = utbot_signal_series(
            m15, key_value=self._params.buy_key, atr_period=self._params.buy_atr_period
        )
        _, sell, sell_stops = utbot_signal_series(
            m15, key_value=self._params.sell_key, atr_period=self._params.sell_atr_period
        )
        current_close = float(m15[-1]["close"])

        if sell[-1]:
            return Signal(
                side=Side.EXIT,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason="UT Bot M15 sell crossover",
                strategy_metadata={"ut_stop": sell_stops[-1]},
            )

        decision_ts = int(m15[-1]["ts"]) + 900_000
        h1_as_of_decision = [
            row for row in h1 if int(row["ts"]) + 3_600_000 <= decision_ts
        ]
        h1_closes = [float(row["close"]) for row in h1_as_of_decision]
        h1_ema200 = ema_last(h1_closes, self._params.trend_ema_period)
        if buy[-1] and h1_ema200 is not None and h1_closes[-1] > h1_ema200:
            return Signal(
                side=Side.LONG,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason="UT Bot M15 buy crossover; H1 close above EMA200",
                suggested_stop=buy_stops[-1],
                strategy_metadata={
                    "ut_stop": buy_stops[-1],
                    "h1_ema200": h1_ema200,
                    "h1_close": h1_closes[-1],
                },
            )
        return None


ENGINE_CLASS = UtBotMtfEngine
