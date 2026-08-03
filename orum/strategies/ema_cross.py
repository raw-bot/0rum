"""Bidirectional EMA9/EMA21 crossover on closed 1h candles, adapted to the
`StrategyEngine` contract.

Raw crossovers alone lose money in chop.  Entry therefore requires a confirmed
cross, directional movement in the same direction, non-falling ADX, sufficient
EMA separation and a normalized slow-EMA slope.  Every price/indicator input is
derived from closed candles supplied by ``PaperEngine``.

Longs have no fixed target; the paper portfolio may attach its causal MFE
ratchet.  Shorts use a complete frozen stop/target bracket because ADR-012
requires every paper short to remain reloadable and protected without the
long-only dynamic-exit manager.  A raw opposite cross does not emit the
directionless ``EXIT`` signal: after confirmation, the opposite directional
entry lets the portfolio reverse the matching position without risking closing
an already aligned position after a reload.

Like every engine, `on_candle` has no notion of an open position (kept out
of `StrategyContext` on purpose, see base.py): it only reports a confirmed,
filtered directional candidate.
"""

from __future__ import annotations

from dataclasses import dataclass

from orum.strategies.base import Side, Signal, StrategyContext


@dataclass(frozen=True)
class EmaCrossParams:
    fast_period: int = 9
    slow_period: int = 21
    confirm_bars: int = 3
    adx_period: int = 14
    adx_threshold: float = 20.0
    swing_lookback: int = 8
    allow_short: bool = False
    require_adx_rising: bool = True
    slope_lookback: int = 3
    min_ema_gap_atr: float = 0.25
    min_slow_slope_atr: float = 0.05
    short_reward_risk: float = 2.0


def _positive_int(config: dict, key: str, default: int) -> int:
    value = config.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else default


def _positive_float(config: dict, key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return default
    return resolved if resolved > 0 else default


def _ema_series(values: list[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _directional_series(
    candles: list[dict], period: int
) -> tuple[list[float], list[float], list[float], list[float]]:
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    n = len(closes)
    tr = [highs[0] - lows[0]]
    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    def _rma(xs: list[float]) -> list[float]:
        alpha = 1.0 / period
        out = [xs[0]]
        for x in xs[1:]:
            out.append(alpha * x + (1.0 - alpha) * out[-1])
        return out

    tr_r, plus_r, minus_r = _rma(tr), _rma(plus_dm), _rma(minus_dm)
    plus_di = [100 * plus_r[i] / tr_r[i] if tr_r[i] else 0.0 for i in range(n)]
    minus_di = [100 * minus_r[i] / tr_r[i] if tr_r[i] else 0.0 for i in range(n)]
    dx = [
        100 * abs(plus_di[i] - minus_di[i]) / (plus_di[i] + minus_di[i]) if (plus_di[i] + minus_di[i]) else 0.0
        for i in range(n)
    ]
    return _rma(dx), plus_di, minus_di, tr_r


class EmaCrossEngine:
    name = "ema_cross"
    version = "1"
    required_timeframes = ["1h"]
    required_indicators = ["ema_fast", "ema_slow", "adx", "plus_di", "minus_di", "atr"]

    def __init__(self) -> None:
        self._params = EmaCrossParams()
        self.warmup_period = self._params.slow_period * 3 + self._params.confirm_bars

    def init(self, config: dict) -> None:
        """`config` is the engine's `params` block from `goal.yaml`/`portfolio.yaml`.
        Unknown or out-of-range keys fall back to the validated defaults rather
        than raising, matching `DonchianEngine.init` / `UtBotMtfEngine.init`."""
        cfg = config if isinstance(config, dict) else {}
        defaults = EmaCrossParams()
        self._params = EmaCrossParams(
            fast_period=_positive_int(cfg, "fast_period", defaults.fast_period),
            slow_period=_positive_int(cfg, "slow_period", defaults.slow_period),
            confirm_bars=_positive_int(cfg, "confirm_bars", defaults.confirm_bars),
            adx_period=_positive_int(cfg, "adx_period", defaults.adx_period),
            adx_threshold=_positive_float(cfg, "adx_threshold", defaults.adx_threshold),
            swing_lookback=_positive_int(cfg, "swing_lookback", defaults.swing_lookback),
            allow_short=(
                cfg.get("allow_short")
                if isinstance(cfg.get("allow_short"), bool)
                else defaults.allow_short
            ),
            require_adx_rising=(
                cfg.get("require_adx_rising")
                if isinstance(cfg.get("require_adx_rising"), bool)
                else defaults.require_adx_rising
            ),
            slope_lookback=_positive_int(
                cfg, "slope_lookback", defaults.slope_lookback
            ),
            min_ema_gap_atr=_positive_float(
                cfg, "min_ema_gap_atr", defaults.min_ema_gap_atr
            ),
            min_slow_slope_atr=_positive_float(
                cfg, "min_slow_slope_atr", defaults.min_slow_slope_atr
            ),
            short_reward_risk=_positive_float(
                cfg, "short_reward_risk", defaults.short_reward_risk
            ),
        )
        self.warmup_period = max(
            self._params.slow_period * 3 + self._params.confirm_bars,
            self._params.adx_period * 3 + self._params.slope_lookback,
        )

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        p = self._params
        candles = context.candles
        if len(candles) < self.warmup_period:
            return None

        closes = [float(c["close"]) for c in candles]
        ema_fast = _ema_series(closes, p.fast_period)
        ema_slow = _ema_series(closes, p.slow_period)
        i = len(candles) - 1

        history = max(p.confirm_bars, p.slope_lookback)
        if i - history < 0:
            return None
        long_held = all(
            ema_fast[i - k] > ema_slow[i - k] for k in range(p.confirm_bars)
        )
        long_was_below = (
            ema_fast[i - p.confirm_bars] <= ema_slow[i - p.confirm_bars]
        )
        short_held = all(
            ema_fast[i - k] < ema_slow[i - k] for k in range(p.confirm_bars)
        )
        short_was_above = (
            ema_fast[i - p.confirm_bars] >= ema_slow[i - p.confirm_bars]
        )
        long_candidate = long_held and long_was_below
        short_candidate = p.allow_short and short_held and short_was_above
        if not (long_candidate or short_candidate):
            return None

        adx, plus_di, minus_di, atr = _directional_series(candles, p.adx_period)
        if atr[i] <= 0 or adx[i] <= p.adx_threshold:
            return None
        if p.require_adx_rising and adx[i] < adx[i - p.slope_lookback]:
            return None

        ema_gap_atr = abs(ema_fast[i] - ema_slow[i]) / atr[i]
        if ema_gap_atr < p.min_ema_gap_atr:
            return None
        slow_slope_atr = (ema_slow[i] - ema_slow[i - p.slope_lookback]) / atr[i]

        lows = [float(c["low"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        recent_low = min(lows[max(0, i - p.swing_lookback + 1) : i + 1])
        recent_high = max(highs[max(0, i - p.swing_lookback + 1) : i + 1])
        price = closes[i]
        metadata = {
            "ema_fast": ema_fast[i],
            "ema_slow": ema_slow[i],
            "adx": adx[i],
            "plus_di": plus_di[i],
            "minus_di": minus_di[i],
            "ema_gap_atr": ema_gap_atr,
            "slow_slope_atr": slow_slope_atr,
        }
        if long_candidate:
            stop = min(ema_slow[i], recent_low)
            if plus_di[i] <= minus_di[i] or slow_slope_atr < p.min_slow_slope_atr:
                return None
            if stop >= price:
                return None
            return Signal(
                side=Side.LONG,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason=(
                    f"EMA{p.fast_period}>EMA{p.slow_period} held {p.confirm_bars}h, "
                    f"ADX={adx[i]:.1f}, +DI={plus_di[i]:.1f}>-DI={minus_di[i]:.1f}, "
                    f"gap={ema_gap_atr:.2f}ATR"
                ),
                suggested_stop=stop,
                strategy_metadata=metadata,
            )

        stop = max(ema_slow[i], recent_high)
        if minus_di[i] <= plus_di[i] or slow_slope_atr > -p.min_slow_slope_atr:
            return None
        if stop <= price:
            return None
        risk = stop - price
        target = price - p.short_reward_risk * risk
        if target <= 0:
            return None
        return Signal(
            side=Side.SHORT,
            symbol=context.symbol,
            timeframe=context.timeframe,
            entry_reason=(
                f"EMA{p.fast_period}<EMA{p.slow_period} held {p.confirm_bars}h, "
                f"ADX={adx[i]:.1f}, -DI={minus_di[i]:.1f}>+DI={plus_di[i]:.1f}, "
                f"gap={ema_gap_atr:.2f}ATR"
            ),
            suggested_stop=stop,
            suggested_take_profit=target,
            strategy_metadata=metadata,
        )


# Lets the registry (orum/strategies/__init__.py) load this engine by module
# path, not just by built-in name.
ENGINE_CLASS = EmaCrossEngine
