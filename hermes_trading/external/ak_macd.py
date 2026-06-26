"""Native Python AK MACD 15m signal brain — the port of pine/ak_macd_15m.pine.

The bot analyzes candles ITSELF and decides BUY/SELL. This module is the PRODUCER
half of the external-signal pipeline: it consumes an OHLCV candle buffer and emits
the SAME versioned JSON payload contract the Pine emitter used. Every downstream
layer (parse -> dedup -> validate -> bracket -> execute) is reused byte-for-byte.
Hermes still DECIDES whether to act; this module only PROPOSES a candidate.

LONG entries are hardened against false MACD bounces (spec:
specs/ak-macd-long-entry-confirmation.md). `flip_up` no longer authorizes an
immediate entry: it ARMS a candidate that must be CONFIRMED, within a window of
`candidate_window_bars` closed bars, by a strictly-increasing MACD sequence over
`confirmation_bars + 1` closed values, while the market regime is not
`unfavorable` and all the existing LONG conditions still hold. The candidate
lifecycle is replayed deterministically over the candle buffer (no hidden state,
fully backtestable). SHORT entries run through the SAME candidate machine,
mirrored: `flip_down` arms a short, confirmed by a strictly-decreasing MACD
sequence while the regime is not `favorable`. ("Unchanged" here means unchanged
vs the prior Python iteration — NOT vs the Pine, which has no candidate machine.)

Faithful recreation of pine/ak_macd_15m.pine (dotMode="slope", EMA baseline):
  baseline = EMA(close, 30); ATR(14) continuation band -> blue / red / gray
  MACD     = EMA(close, 12) - EMA(close, 26); slope dots; flip = slope turn
  volume   > SMA(volume, 9)
  SL/TP frozen by Hermes' bracket.py (widest of baseline/swing, TP = 1.5R).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from hermes_trading.dsl import indicators as ind
from hermes_trading.market_regime import rolling_return_regime

PAYLOAD_VERSION = "py_ak_macd_15m_v1"
DEFAULT_SOURCE = "local"
DEFAULT_STRATEGY_ID = "ak_macd_15m_v1"

# Lifecycle actions logged by the producer (Requirement 8).
ACTION_NO_SETUP = "no_setup"
ACTION_ARMED = "candidate_armed"
ACTION_CONFIRMED = "candidate_confirmed"
ACTION_EXPIRED = "candidate_expired"
ACTION_REJECTED_REGIME = "rejected_regime"
ACTION_REJECTED_MACD = "rejected_macd_not_rising"
ACTION_REJECTED_CONDITIONS = "rejected_conditions"


@dataclass(frozen=True)
class AkMacdParams:
    """Mirrors the Pine inputs (defaults = pine/ak_macd_15m.pine defaults), plus the
    LONG confirmation knobs (overridable via strategy.yaml `ak_macd:` section)."""

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    base_len: int = 30
    atr_len: int = 14
    atr_mult: float = 0.2
    vol_ma_len: int = 9
    trend_look: int = 50
    pull_look: int = 10
    swing_look: int = 10
    allow_short: bool = True
    # --- LONG confirmation (anti false-bounce) ---
    confirmation_bars: int = 2       # require macd[t] > macd[t-1] > ... > macd[t-confirmation_bars]
    candidate_window_bars: int = 2   # confirmation must land within W closed bars after the flip
    regime_filter: bool = True       # block LONG when rolling_return_regime == "unfavorable"
    require_candle_direction: bool = True  # entry bar must close in the trade direction (green=long / red=short)

    @property
    def warmup(self) -> int:
        return self.macd_slow + self.trend_look + 5


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not math.isnan(value)


def _num_or_none(value):
    return float(value) if _is_num(value) else None


def _ema_series(values: list[float], period: int) -> list[float]:
    """EMA over an arbitrary value series (TA-Lib seeding: SMA of first `period`
    non-NaN values), NaN during warm-up. Used for the MACD signal line."""
    out = [float("nan")] * len(values)
    valid = [(i, v) for i, v in enumerate(values) if _is_num(v)]
    if len(valid) < period:
        return out
    k = 2.0 / (period + 1.0)
    seed_idx = valid[period - 1][0]
    prev = sum(v for _, v in valid[:period]) / period
    out[seed_idx] = prev
    for i, v in valid[period:]:
        prev = (v - prev) * k + prev
        out[i] = prev
    return out


def _sma_series(values: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(values)
    if len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        if any(not _is_num(v) for v in window):
            continue
        out[i] = sum(window) / period
    return out


@dataclass(frozen=True)
class AkMacdState:
    """Per-bar series computed once, reused by the entry logic and for audit."""

    closes: list[float]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    volumes: list[float]
    baseline: list[float]
    macd: list[float]
    signal: list[float]
    vol_ma: list[float]
    is_blue: list[bool]
    is_red: list[bool]
    is_gray: list[bool]


def compute_state(candles: list[dict], params: AkMacdParams) -> AkMacdState:
    closes = [float(c["close"]) for c in candles]
    # Open is needed for the entry-candle direction gate. Default to NaN when a
    # caller synthesizes close-only candles, so the gate passes (never blocks on
    # missing data) rather than treating open==close as a non-directional bar.
    opens = [float(c["open"]) if "open" in c else float("nan") for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    volumes = [float(c.get("volume", 0.0)) for c in candles]

    ema_fast = ind.ema(candles, params.macd_fast)
    ema_slow = ind.ema(candles, params.macd_slow)
    macd = [
        f - s if _is_num(f) and _is_num(s) else float("nan")
        for f, s in zip(ema_fast, ema_slow)
    ]
    signal = _ema_series(macd, params.macd_signal)
    baseline = ind.ema(candles, params.base_len)
    atr = ind.atr(candles, params.atr_len)
    vol_ma = _sma_series(volumes, params.vol_ma_len)

    is_blue: list[bool] = []
    is_red: list[bool] = []
    is_gray: list[bool] = []
    for i in range(len(candles)):
        if _is_num(baseline[i]) and _is_num(atr[i]):
            band = atr[i] * params.atr_mult
            blue = closes[i] > baseline[i] + band
            red = closes[i] < baseline[i] - band
        else:
            blue = red = False
        is_blue.append(blue)
        is_red.append(red)
        is_gray.append(not blue and not red)

    return AkMacdState(
        closes=closes, opens=opens, highs=highs, lows=lows, volumes=volumes,
        baseline=baseline, macd=macd, signal=signal, vol_ma=vol_ma,
        is_blue=is_blue, is_red=is_red, is_gray=is_gray,
    )


def _flip_up(macd: list[float], t: int) -> bool:
    if t < 2 or not all(_is_num(macd[i]) for i in (t, t - 1, t - 2)):
        return False
    return macd[t] > macd[t - 1] and macd[t - 1] <= macd[t - 2]


def _flip_down(macd: list[float], t: int) -> bool:
    if t < 2 or not all(_is_num(macd[i]) for i in (t, t - 1, t - 2)):
        return False
    return macd[t] < macd[t - 1] and macd[t - 1] >= macd[t - 2]


def _strictly_increasing(macd: list[float], t: int, n: int) -> bool:
    """True iff the last n+1 closed MACD values are strictly increasing:
    macd[t] > macd[t-1] > ... > macd[t-n]. Any NaN or tie -> False."""
    if t < n:
        return False
    values = macd[t - n : t + 1]
    if any(not _is_num(v) for v in values):
        return False
    return all(values[i] > values[i - 1] for i in range(1, len(values)))


def _strictly_decreasing(macd: list[float], t: int, n: int) -> bool:
    """Mirror of _strictly_increasing: macd[t] < macd[t-1] < ... < macd[t-n]."""
    if t < n:
        return False
    values = macd[t - n : t + 1]
    if any(not _is_num(v) for v in values):
        return False
    return all(values[i] < values[i - 1] for i in range(1, len(values)))


def _sequenced_long(st: AkMacdState, t: int, params: AkMacdParams) -> bool:
    """Strict order: a BLUE trend bar, then a GRAY/RED pullback bar, then now."""
    pull_start = max(0, t - params.pull_look)
    for p in range(t, pull_start - 1, -1):
        if st.is_gray[p] or st.is_red[p]:
            trend_start = max(0, p - params.trend_look)
            if any(st.is_blue[b] for b in range(trend_start, p)):
                return True
            return False
    return False


def _sequenced_short(st: AkMacdState, t: int, params: AkMacdParams) -> bool:
    pull_start = max(0, t - params.pull_look)
    for p in range(t, pull_start - 1, -1):
        if st.is_gray[p] or st.is_blue[p]:
            trend_start = max(0, p - params.trend_look)
            if any(st.is_red[b] for b in range(trend_start, p)):
                return True
            return False
    return False


def _regime_at(closes: list[float], t: int) -> tuple[str, float]:
    """Regime label + rolling-return value over the 20 closes ending at bar t."""
    r = rolling_return_regime(closes[: t + 1])
    return r["label"], r["return_pct"]


def _candle_in_direction(st: AkMacdState, t: int, params: AkMacdParams, *, long: bool) -> bool:
    """Entry-candle price-action gate: the bar the signal fires on must close in
    the trade direction (green = close>open for long, red = close<open for short).
    Disabled when require_candle_direction is False, or when the open is unknown
    (close-only synthesized candles) — never blocks on missing data."""
    if not params.require_candle_direction or not _is_num(st.opens[t]):
        return True
    return st.closes[t] > st.opens[t] if long else st.closes[t] < st.opens[t]


def _other_long_conditions(st: AkMacdState, t: int, params: AkMacdParams) -> bool:
    """The existing non-MACD-confirmation LONG conditions (Requirement 5)."""
    return (
        _is_num(st.macd[t]) and st.macd[t] > 0.0
        and _is_num(st.baseline[t]) and st.closes[t] > st.baseline[t]
        and _is_num(st.vol_ma[t]) and st.volumes[t] > st.vol_ma[t]
        and _sequenced_long(st, t, params)
        and _candle_in_direction(st, t, params, long=True)
    )


def _other_short_conditions(st: AkMacdState, t: int, params: AkMacdParams) -> bool:
    """Mirror of _other_long_conditions: the non-MACD-confirmation SHORT conditions."""
    return (
        _is_num(st.macd[t]) and st.macd[t] < 0.0
        and _is_num(st.baseline[t]) and st.closes[t] < st.baseline[t]
        and _is_num(st.vol_ma[t]) and st.volumes[t] > st.vol_ma[t]
        and _sequenced_short(st, t, params)
        and _candle_in_direction(st, t, params, long=False)
    )


def _build_payload(st: AkMacdState, timestamps: list[int], t: int, event: str,
                   params: AkMacdParams, symbol: str, timeframe: str,
                   source: str, strategy: str) -> dict:
    swing_start = max(0, t - params.swing_look + 1)
    color = "blue" if st.is_blue[t] else "red" if st.is_red[t] else "gray"
    return {
        "source": source,
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
        "event": event,
        "bar_time": int(timestamps[t]),
        "price": st.closes[t],
        "version": PAYLOAD_VERSION,
        "macd": st.macd[t],
        "signal": st.signal[t] if _is_num(st.signal[t]) else None,
        "baseline": st.baseline[t],
        "base_color": color,
        "vol_ratio": st.volumes[t] / st.vol_ma[t] if _is_num(st.vol_ma[t]) and st.vol_ma[t] else None,
        "baseline_at_entry": st.baseline[t],
        "recent_low": min(st.lows[swing_start : t + 1]),
        "recent_high": max(st.highs[swing_start : t + 1]),
    }


@dataclass(frozen=True)
class AkMacdVerdict:
    """The decision at the latest closed bar, with audit fields for logging."""

    action: str
    payload: dict | None = None
    reason: str = ""
    event: str | None = None
    side: str | None = None   # "long" | "short" — direction of the candidate
    bar_time: int | None = None
    macd: tuple = field(default_factory=tuple)   # (macd[t], macd[t-1], macd[t-2])
    regime: str | None = None
    rolling_return: float | None = None
    remaining_window: int | None = None


def run_candidate_machine(
    st: AkMacdState, timestamps: list[int], params: AkMacdParams, *,
    symbol: str, timeframe: str = "15m", source: str = DEFAULT_SOURCE,
    strategy: str = DEFAULT_STRATEGY_ID,
) -> AkMacdVerdict:
    """Replay the candidate lifecycle deterministically over the whole buffer and
    return the verdict at the LAST bar. At most ONE candidate is active, carrying a
    direction: ``flip_up`` arms a LONG candidate, ``flip_down`` arms a SHORT one; an
    opposite flip switches sides. A candidate confirms only on a sustained MACD move
    (strictly increasing for long / decreasing for short) within its window, with the
    regime not adverse (LONG blocked when ``unfavorable``, SHORT when ``favorable``).
    Pure: no I/O, no clock."""
    n = len(st.macd)
    W = params.candidate_window_bars
    cb = params.confirmation_bars
    candidate: tuple[int, str] | None = None  # (armed_index, side)
    verdict = AkMacdVerdict(action=ACTION_NO_SETUP, bar_time=(int(timestamps[-1]) if timestamps else None))

    for t in range(2, n):
        action = ACTION_NO_SETUP
        reason = ""
        payload = None
        event = None
        side = candidate[1] if candidate is not None else None
        regime_label: str | None = None
        regime_ret: float | None = None
        c_at_start = candidate

        fu = _flip_up(st.macd, t)
        fd = _flip_down(st.macd, t)
        # Regime is computed (and logged) for EVERY bar so every lifecycle entry
        # carries the label + rolling-return value (Req 8).
        if params.regime_filter:
            regime_label, regime_ret = _regime_at(st.closes, t)

        # A flip ARMS (or switches) a candidate in its direction. A flip bar can
        # never itself confirm; flip_up and flip_down are mutually exclusive.
        if fu:
            candidate, side = (t, "long"), "long"
            action, reason = ACTION_ARMED, "flip_up armed long candidate"
        elif fd and params.allow_short:
            candidate, side = (t, "short"), "short"
            action, reason = ACTION_ARMED, "flip_down armed short candidate"
        elif candidate is not None:
            c, side = candidate
            if t > c + W:
                action, reason = ACTION_EXPIRED, "window exceeded without confirmation"
                candidate = None
            elif side == "long":
                if not (_is_num(st.macd[t]) and _is_num(st.macd[t - 1]) and st.macd[t] > st.macd[t - 1]):
                    action, reason = ACTION_EXPIRED, "macd rising sequence broken"
                    candidate = None
                elif not _strictly_increasing(st.macd, t, cb):
                    action, reason = ACTION_REJECTED_MACD, f"macd not strictly increasing over {cb + 1} bars"
                elif not _other_long_conditions(st, t, params):
                    action, reason = ACTION_REJECTED_CONDITIONS, "a non-MACD long condition not met"
                elif params.regime_filter and regime_label == "unfavorable":
                    action, reason = ACTION_REJECTED_REGIME, f"regime unfavorable (rolling return {regime_ret:.4f})"
                else:
                    action, reason, event = ACTION_CONFIRMED, "macd confirmed; all long conditions met", "BUY_CANDIDATE"
                    payload = _build_payload(st, timestamps, t, event, params, symbol, timeframe, source, strategy)
                    candidate = None
            else:  # short — mirror of long
                if not (_is_num(st.macd[t]) and _is_num(st.macd[t - 1]) and st.macd[t] < st.macd[t - 1]):
                    action, reason = ACTION_EXPIRED, "macd falling sequence broken"
                    candidate = None
                elif not _strictly_decreasing(st.macd, t, cb):
                    action, reason = ACTION_REJECTED_MACD, f"macd not strictly decreasing over {cb + 1} bars"
                elif not _other_short_conditions(st, t, params):
                    action, reason = ACTION_REJECTED_CONDITIONS, "a non-MACD short condition not met"
                elif params.regime_filter and regime_label == "favorable":
                    action, reason = ACTION_REJECTED_REGIME, f"regime favorable (rolling return {regime_ret:.4f})"
                else:
                    action, reason, event = ACTION_CONFIRMED, "macd confirmed; all short conditions met", "SELL_CANDIDATE"
                    payload = _build_payload(st, timestamps, t, event, params, symbol, timeframe, source, strategy)
                    candidate = None

        # Window position for THIS bar's candidate (armed this bar, or active at bar
        # start), captured even on terminal actions (0 at expiry).
        if fu or (fd and params.allow_short):
            remaining = W
        elif c_at_start is not None:
            remaining = max(0, c_at_start[0] + W - t)
        else:
            remaining = None
        verdict = AkMacdVerdict(
            action=action, payload=payload, reason=reason, event=event, side=side,
            bar_time=int(timestamps[t]),
            macd=(_num_or_none(st.macd[t]),
                  _num_or_none(st.macd[t - 1]) if t >= 1 else None,
                  _num_or_none(st.macd[t - 2]) if t >= 2 else None),
            regime=regime_label, rolling_return=regime_ret, remaining_window=remaining,
        )

    return verdict


def evaluate_ak_macd_verdict(
    candles: list[dict], params: AkMacdParams | None = None, *,
    symbol: str, timeframe: str = "15m", source: str = DEFAULT_SOURCE,
    strategy: str = DEFAULT_STRATEGY_ID,
) -> AkMacdVerdict:
    """Full lifecycle verdict for the LAST candle. Caller MUST pass only CLOSED
    candles (the producer drops the forming bar)."""
    params = params or AkMacdParams()
    if len(candles) < params.warmup:
        return AkMacdVerdict(
            action=ACTION_NO_SETUP, reason="insufficient warm-up",
            bar_time=(int(candles[-1]["ts"]) if candles else None),
        )
    st = compute_state(candles, params)
    timestamps = [int(c["ts"]) for c in candles]
    return run_candidate_machine(
        st, timestamps, params, symbol=symbol, timeframe=timeframe,
        source=source, strategy=strategy,
    )


def evaluate_ak_macd(
    candles: list[dict], params: AkMacdParams | None = None, *,
    symbol: str, timeframe: str = "15m", source: str = DEFAULT_SOURCE,
    strategy: str = DEFAULT_STRATEGY_ID,
) -> dict | None:
    """Backward-compatible entry-payload accessor: returns the emitted payload for
    the last bar (confirmed LONG or SHORT), or None. Lifecycle detail is available
    via evaluate_ak_macd_verdict."""
    return evaluate_ak_macd_verdict(
        candles, params, symbol=symbol, timeframe=timeframe, source=source, strategy=strategy,
    ).payload
