"""Heikin Ashi trend-conditional engine (long-only), adapted to the
`StrategyEngine` contract.

Port of the TradingView-validated "TradingStrat1 — HA + EMA zone + RSI50"
strategy (backtests 2026-07-16, script saved on the operator's TradingView):
BTC/USDT 4h 2024→2026 +30 % / PF 1.33 / DD 11.5 %, XAUUSD 4h +22 % / PF 1.22,
losing on ranging markets (EURUSD 4h −13 %). The ablation test showed the
Heikin Ashi flip is the load-bearing part: the same rules with plain candle
color flips drop to PF 0.93 on gold. Hence this engine trades ONLY when its
trend gate is open — it is a regime-conditional module, not a standalone edge.

Signal, on the last CLOSED candle (`candles[-1]`), all conditions required:
  trend gate : EMA(len)(high) and EMA(len)(low) both above their value
               `slope_len` bars ago (the "zone" slopes up; flat/down = no trade)
  momentum   : RSI(rsi_len) > 50
  pullback   : some low within the last `touch_window`+1 bars touched the
               zone top (low <= EMA(high))
  trigger    : Heikin Ashi candle flips red -> green on this close
  levels     : stop = lowest low of the last `swing_len` bars,
               take profit = close + rr * (close - stop)

Execution notes, deliberately mirroring the validated Pine script:
  * HA candles are computed from real OHLC but ORDERS use real prices only —
    trading at HA prices is the classic Heikin Ashi backtest fraud.
  * The flip is only ever judged on a closed candle (intrabar HA repaints).
  * Long-only: the paper broker is long-only (`paper_broker.Position`), and the
    validated portfolio policy is `allow_short: false` everywhere. A downtrend
    produces no signal, never a SHORT.
  * Exits are the protective bracket (suggested_stop/suggested_take_profit,
    `exit_policy: structural_bracket`); the engine emits no EXIT of its own,
    exactly like the backtest.

Like every engine, `on_candle` has no notion of an open position: it reports
what the setup says; opening while already holding is a caller-side no-op.
"""

from __future__ import annotations

from orum.strategies.base import Side, Signal, StrategyContext

_DEFAULT_EMA_LEN = 20
_DEFAULT_SLOPE_LEN = 5
_DEFAULT_TOUCH_WINDOW = 5
_DEFAULT_SWING_LEN = 10
_DEFAULT_RR = 3.0
_DEFAULT_RSI_LEN = 14


def _pos_int(cfg: dict, key: str, default: int) -> int:
    value = cfg.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else default


def _pos_float(cfg: dict, key: str, default: float) -> float:
    value = cfg.get(key, default)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return default


def _ema(values: list[float], length: int) -> list[float]:
    """Standard EMA seeded with the SMA of the first `length` values (same
    seeding as TradingView's `ta.ema`, which the backtest used)."""
    n = len(values)
    out = [float("nan")] * n
    if n < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    alpha = 2.0 / (length + 1.0)
    for i in range(length, n):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi_last(closes: list[float], length: int) -> float:
    """Wilder RSI of the last close (TradingView `ta.rsi` semantics)."""
    if len(closes) < length + 1:
        return float("nan")
    gains, losses = 0.0, 0.0
    for i in range(1, length + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / length
    avg_loss = losses / length
    alpha = 1.0 / length
    for i in range(length + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = alpha * max(delta, 0.0) + (1.0 - alpha) * avg_gain
        avg_loss = alpha * max(-delta, 0.0) + (1.0 - alpha) * avg_loss
    if avg_loss == 0.0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _ha_green_flags(candles: list[dict]) -> list[bool]:
    """True where the Heikin Ashi candle is green. HA values are derived from
    real OHLC; they are used for the trigger only, never as trade prices."""
    out: list[bool] = []
    ha_open = 0.0
    for i, candle in enumerate(candles):
        o, h, l, c = (float(candle["open"]), float(candle["high"]),
                      float(candle["low"]), float(candle["close"]))
        ha_close = (o + h + l + c) / 4.0
        ha_open = (o + c) / 2.0 if i == 0 else (ha_open + prev_ha_close) / 2.0
        out.append(ha_close > ha_open)
        prev_ha_close = ha_close
    return out


class HaTrendEngine:
    name = "ha_trend"
    version = "1"
    required_timeframes: list[str] = []
    required_indicators = ["ema_high", "ema_low", "rsi", "heikin_ashi"]

    def __init__(self) -> None:
        self._ema_len = _DEFAULT_EMA_LEN
        self._slope_len = _DEFAULT_SLOPE_LEN
        self._touch_window = _DEFAULT_TOUCH_WINDOW
        self._swing_len = _DEFAULT_SWING_LEN
        self._rr = _DEFAULT_RR
        self._rsi_len = _DEFAULT_RSI_LEN
        self.warmup_period = self._warmup()

    def _warmup(self) -> int:
        # EMA needs its seed plus the slope lookback; Wilder RSI needs a long
        # runway to converge (5x length is the usual rule of thumb).
        return max(self._ema_len + self._slope_len, 5 * self._rsi_len, self._swing_len) + 2

    def init(self, config: dict) -> None:
        """`config` is the engine's `params` block. Unknown or out-of-range
        keys fall back to the backtested defaults rather than raising,
        matching `DonchianEngine.init`."""
        cfg = config if isinstance(config, dict) else {}
        self._ema_len = _pos_int(cfg, "ema_len", _DEFAULT_EMA_LEN)
        self._slope_len = _pos_int(cfg, "slope_len", _DEFAULT_SLOPE_LEN)
        self._touch_window = _pos_int(cfg, "touch_window", _DEFAULT_TOUCH_WINDOW)
        self._swing_len = _pos_int(cfg, "swing_len", _DEFAULT_SWING_LEN)
        self._rr = _pos_float(cfg, "rr", _DEFAULT_RR)
        self._rsi_len = _pos_int(cfg, "rsi_len", _DEFAULT_RSI_LEN)
        self.warmup_period = self._warmup()

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        candles = context.candles
        if len(candles) < self.warmup_period:
            return None  # not enough history for stable EMA slope / RSI

        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]

        ema_high = _ema(highs, self._ema_len)
        ema_low = _ema(lows, self._ema_len)

        # Trend gate: the whole zone must slope up; flat or down = stay out.
        prev = -1 - self._slope_len
        if not (ema_high[-1] > ema_high[prev] and ema_low[-1] > ema_low[prev]):
            return None

        rsi = _rsi_last(closes, self._rsi_len)
        if not rsi > 50.0:
            return None

        # Pullback: price touched the zone top within the touch window.
        touched = any(
            lows[i] <= ema_high[i]
            for i in range(len(candles) - 1 - self._touch_window, len(candles))
        )
        if not touched:
            return None

        # Trigger: Heikin Ashi flips red -> green on this closed candle.
        ha_green = _ha_green_flags(candles)
        if not (ha_green[-1] and not ha_green[-2]):
            return None

        close = closes[-1]
        stop = min(lows[-self._swing_len:])
        risk = close - stop
        if risk <= 0:
            return None  # degenerate: close at/below the swing low

        return Signal(
            side=Side.LONG,
            symbol=context.symbol,
            timeframe=context.timeframe,
            entry_reason=(
                f"HA flip green in uptrend: zone slope up over {self._slope_len} bars, "
                f"RSI {rsi:.1f} > 50, pullback touch within {self._touch_window} bars"
            ),
            suggested_stop=stop,
            suggested_take_profit=close + self._rr * risk,
            strategy_metadata={
                "rsi": rsi,
                "ema_high": ema_high[-1],
                "ema_low": ema_low[-1],
                "swing_low": stop,
                "rr": self._rr,
            },
        )


# Lets the registry (orum/strategies/__init__.py) load this engine by module
# path, not just by built-in name.
ENGINE_CLASS = HaTrendEngine
