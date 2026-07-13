"""Deterministic long-only KAMA squeeze challenger.

The module is pure: it consumes normalized, oldest-to-newest closed candles and
returns indicator state. It performs no I/O, sizing, persistence, or execution.
The executable definitions are frozen in ADR-001.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from orum.strategies.base import Side


@dataclass(frozen=True)
class KamaSqueezeParams:
    efficiency_length: int = 32
    fast_length: int = 2
    slow_length: int = 50
    slope_lookback: int = 1
    minimum_efficiency: float = 0.20
    squeeze_length: int = 16
    bb_multiplier: float = 2.0
    kc_multiplier: float = 1.5
    minimum_squeeze_bars: int = 2
    atr_length: int = 14
    momentum_length: int = 16
    initial_stop_atr: float = 2.8
    trailing_stop_atr: float = 5.0


@dataclass
class KamaSqueezeState:
    closes: list[float]
    atr: list[float]
    efficiency_ratio: list[float]
    kama: list[float]
    squeeze_on: list[bool]
    squeeze_release: list[bool]
    momentum: list[float]


@dataclass(frozen=True)
class LongPositionState:
    entry_index: int
    entry_price: float
    initial_stop: float
    effective_stop: float
    highest_close: float


@dataclass
class KamaReplay:
    signals: list[Side | None]
    exit_reasons: list[str | None]
    entry_fills: list[tuple[int, float]]
    exit_fills: list[tuple[int, float]]
    position: LongPositionState | None


def linear_regression_endpoint(values: list[float]) -> float:
    """Return the ordinary-least-squares line value at the last sample."""
    n = len(values)
    if n == 0:
        return math.nan
    if n == 1:
        return float(values[0])
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    denominator = sum((x - x_mean) ** 2 for x in range(n))
    slope = sum((x - x_mean) * (values[x] - y_mean) for x in range(n)) / denominator
    intercept = y_mean - slope * x_mean
    return intercept + slope * (n - 1)


def squeeze_release_flags(squeeze_on: list[bool], *, minimum_bars: int) -> list[bool]:
    releases = [False] * len(squeeze_on)
    run = 0
    for i, active in enumerate(squeeze_on):
        if active:
            run += 1
        else:
            releases[i] = run >= minimum_bars
            run = 0
    return releases


def _true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    if not closes:
        return []
    out = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        out.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return out


def _wilder(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    alpha = 1.0 / length
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _efficiency_ratio(closes: list[float], length: int) -> list[float]:
    out = [0.0] * len(closes)
    for i in range(length, len(closes)):
        direction = abs(closes[i] - closes[i - length])
        volatility = sum(abs(closes[j] - closes[j - 1]) for j in range(i - length + 1, i + 1))
        out[i] = direction / volatility if volatility > 0 else 0.0
    return out


def _kama(closes: list[float], efficiency: list[float], params: KamaSqueezeParams) -> list[float]:
    if not closes:
        return []
    fast = 2.0 / (params.fast_length + 1.0)
    slow = 2.0 / (params.slow_length + 1.0)
    out = [closes[0]]
    for i in range(1, len(closes)):
        smoothing = (efficiency[i] * (fast - slow) + slow) ** 2
        out.append(out[-1] + smoothing * (closes[i] - out[-1]))
    return out


def _squeeze_state(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    true_ranges: list[float],
    params: KamaSqueezeParams,
) -> list[bool]:
    length = params.squeeze_length
    out = [False] * len(closes)
    for i in range(length - 1, len(closes)):
        close_window = closes[i - length + 1:i + 1]
        basis = sum(close_window) / length
        variance = sum((value - basis) ** 2 for value in close_window) / length
        deviation = math.sqrt(variance)
        bb_upper = basis + params.bb_multiplier * deviation
        bb_lower = basis - params.bb_multiplier * deviation
        range_mean = sum(true_ranges[i - length + 1:i + 1]) / length
        kc_upper = basis + params.kc_multiplier * range_mean
        kc_lower = basis - params.kc_multiplier * range_mean
        out[i] = bb_lower > kc_lower and bb_upper < kc_upper
    return out


def _momentum(
    highs: list[float], lows: list[float], closes: list[float], length: int,
) -> list[float]:
    raw = [math.nan] * len(closes)
    for i in range(length - 1, len(closes)):
        start = i - length + 1
        high = max(highs[start:i + 1])
        low = min(lows[start:i + 1])
        close_sma = sum(closes[start:i + 1]) / length
        raw[i] = closes[i] - (((high + low) / 2.0 + close_sma) / 2.0)

    out = [math.nan] * len(closes)
    for i in range(2 * length - 2, len(closes)):
        window = raw[i - length + 1:i + 1]
        if all(math.isfinite(value) for value in window):
            out[i] = linear_regression_endpoint(window)
    return out


def compute_kama_state(
    candles: list[dict], params: KamaSqueezeParams | None = None,
) -> KamaSqueezeState:
    params = params or KamaSqueezeParams()
    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]
    true_ranges = _true_ranges(highs, lows, closes)
    efficiency = _efficiency_ratio(closes, params.efficiency_length)
    squeeze_on = _squeeze_state(highs, lows, closes, true_ranges, params)
    return KamaSqueezeState(
        closes=closes,
        atr=_wilder(true_ranges, params.atr_length),
        efficiency_ratio=efficiency,
        kama=_kama(closes, efficiency, params),
        squeeze_on=squeeze_on,
        squeeze_release=squeeze_release_flags(
            squeeze_on, minimum_bars=params.minimum_squeeze_bars,
        ),
        momentum=_momentum(highs, lows, closes, params.momentum_length),
    )


def entry_at(state: KamaSqueezeState, index: int, params: KamaSqueezeParams | None = None) -> bool:
    params = params or KamaSqueezeParams()
    previous = index - params.slope_lookback
    if previous < 0 or index >= len(state.closes):
        return False
    momentum = state.momentum[index]
    prior_momentum = state.momentum[index - 1] if index > 0 else math.nan
    return (
        state.squeeze_release[index]
        and math.isfinite(momentum)
        and math.isfinite(prior_momentum)
        and momentum > 0.0
        and momentum > prior_momentum
        and state.closes[index] > state.kama[index]
        and state.kama[index] > state.kama[previous]
        and state.efficiency_ratio[index] > params.minimum_efficiency
    )


def open_long_position(
    *,
    fill_price: float,
    signal_atr: float,
    entry_index: int,
    params: KamaSqueezeParams | None = None,
) -> LongPositionState:
    params = params or KamaSqueezeParams()
    if not (fill_price > 0.0 and signal_atr > 0.0):
        raise ValueError("fill_price and signal_atr must be positive")
    initial_stop = fill_price - params.initial_stop_atr * signal_atr
    return LongPositionState(
        entry_index=entry_index,
        entry_price=fill_price,
        initial_stop=initial_stop,
        effective_stop=initial_stop,
        highest_close=fill_price,
    )


def update_long_position(
    position: LongPositionState,
    *,
    close: float,
    atr: float,
    kama: float,
    momentum: float,
    params: KamaSqueezeParams | None = None,
) -> tuple[LongPositionState, str | None]:
    """Advance a long using confirmed-close information only.

    The returned reason is an exit intent for the next executable bar; this
    function never assumes an intrabar fill.
    """
    params = params or KamaSqueezeParams()
    highest_close = max(position.highest_close, close)
    trail_candidate = highest_close - params.trailing_stop_atr * atr
    effective_stop = max(
        position.effective_stop,
        position.initial_stop,
        trail_candidate,
    )
    updated = replace(
        position,
        highest_close=highest_close,
        effective_stop=effective_stop,
    )
    if close <= effective_stop:
        return updated, "atr_trail_close"
    if close < kama and momentum < 0.0:
        return updated, "kama_negative_momentum"
    return updated, None


def replay_strategy(
    candles: list[dict],
    *,
    params: KamaSqueezeParams | None = None,
    state: KamaSqueezeState | None = None,
) -> KamaReplay:
    """Rebuild strategy state from candles using explicit next-bar fills."""
    params = params or KamaSqueezeParams()
    state = state or compute_kama_state(candles, params)
    if len(candles) != len(state.closes):
        raise ValueError("candles and precomputed state must have the same length")

    signals: list[Side | None] = [None] * len(candles)
    exit_reasons: list[str | None] = [None] * len(candles)
    entry_fills: list[tuple[int, float]] = []
    exit_fills: list[tuple[int, float]] = []
    pending_entry: int | None = None
    pending_exit = False
    position: LongPositionState | None = None

    for i, candle in enumerate(candles):
        exited_at_open = False
        if pending_exit:
            exit_fills.append((i, float(candle["open"])))
            position = None
            pending_exit = False
            exited_at_open = True

        if pending_entry is not None:
            fill_price = float(candle["open"])
            position = open_long_position(
                fill_price=fill_price,
                signal_atr=state.atr[pending_entry],
                entry_index=i,
                params=params,
            )
            entry_fills.append((i, fill_price))
            pending_entry = None

        if position is not None:
            position, reason = update_long_position(
                position,
                close=state.closes[i],
                atr=state.atr[i],
                kama=state.kama[i],
                momentum=state.momentum[i],
                params=params,
            )
            if reason is not None:
                signals[i] = Side.EXIT
                exit_reasons[i] = reason
                pending_exit = True
        elif not exited_at_open and entry_at(state, i, params):
            signals[i] = Side.LONG
            if i + 1 < len(candles):
                pending_entry = i

    return KamaReplay(
        signals=signals,
        exit_reasons=exit_reasons,
        entry_fills=entry_fills,
        exit_fills=exit_fills,
        position=position,
    )

