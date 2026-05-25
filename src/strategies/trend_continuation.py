"""Trend Continuation strategy — EMA(50/200) trend filter, pullback EMA touch, M15 PA entry.

Per CLAUDE.md §9.3 — Enter on pullbacks within established trends confirmed by price action.
"""

from __future__ import annotations

import numpy as np
import structlog

from src.indicators.ema import ema
from src.models.signal_data import CandidateSignal, Direction, StrategyName, Timeframe
from src.strategies.base import AbstractStrategy

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Confidence weights — must sum to 1.0 (D-01: linear weighted sum, no sigmoid)
# ---------------------------------------------------------------------------
# W_TREND_STRENGTH = 0.45 — trend quality is the dominant factor for this strategy
#   (ADX(14) on H1 normalised: adx / 50.0 capped at 1.0; ADX > 25 = strong trend)
# W_PULLBACK_QUALITY = 0.35 — clean pullback to EMA zone without overextension
#   (distance of lowest touch from EMA normalised: 1.0 - min(gap/atr, 1.0))
# W_PA_PATTERN = 0.20 — price action confirmation pattern quality
#   (engulfing=1.0, pin_bar=0.7, inside_bar=0.5)
# Weights sum = 0.45 + 0.35 + 0.20 = 1.00
W_TREND_STRENGTH = 0.45
W_PULLBACK_QUALITY = 0.35
W_PA_PATTERN = 0.20

# Structural EMA periods — NEVER optimised (CLAUDE.md §17 anti-pattern #2)
_EMA_FAST_STRUCTURAL = 50
_EMA_SLOW_STRUCTURAL = 200

# Minimum H1 candles required: EMA(200) needs 200 values + buffer
_MIN_H1_CANDLES = 205


class TrendContinuationStrategy(AbstractStrategy):
    """Enter on pullbacks to a dynamic EMA zone within EMA(50/200)-confirmed trends.

    Concept:
    1. Trend filter: EMA(50) > EMA(200) on H1 = uptrend; vice-versa = downtrend.
    2. Pullback: price touches EMA(pullback_ema) from the correct side in last 5 H1 candles.
    3. Entry trigger: M15 price action confirmation (engulfing, pin bar, or inside bar breakout).
    4. SL below/above the pullback low/high ± sl_atr_mult×ATR14(H1).
    5. TP1 = tp_risk_mult × risk distance from entry.
    6. TP2 = nearest swing high/low beyond TP1, or TP1 × 1.5 fallback.

    PARAM_RANGES contains exactly 3 optimisable parameters per CLAUDE.md §17:
    - pullback_ema:  EMA period for pullback zone identification (15–55)
    - sl_atr_mult:   SL distance below pullback low (0.3–1.0 × ATR14)
    - tp_risk_mult:  TP1 as multiple of entry→SL risk (1.2–3.0×)

    Fixed structural parameters (never optimised, per CLAUDE.md §17):
    - Trend EMAs: EMA(50) and EMA(200) — structural periods
    - Swing detection order=10 for TP2 lookup
    - Pattern weights: engulfing=1.0, pin_bar=0.7, inside_bar=0.5

    Attributes:
        STRATEGY_NAME: Canonical strategy identifier used by StrategyRunner DB queries.
        PARAM_RANGES: Dict of optimisable param name → (min, max) range.
    """

    STRATEGY_NAME = "trend_continuation"

    PARAM_RANGES: dict[str, tuple[float, float]] = {
        "pullback_ema": (15.0, 55.0),  # EMA period for pullback zone identification
        "sl_atr_mult":  (0.3, 1.0),    # SL distance below pullback low (×ATR14)
        "tp_risk_mult": (1.2, 3.0),    # TP1 as multiple of entry→SL risk
    }

    def __init__(self, params: dict) -> None:
        """Initialise strategy, filling missing params with PARAM_RANGES midpoints.

        Args:
            params: Dict of param_name → value from the optimizer or StrategyRunner.
                    Missing keys are filled with the midpoint of PARAM_RANGES (D-05).
        """
        # T-03-05: fill missing params with midpoint defaults, log warning
        resolved: dict = {}
        for key, (lo, hi) in self.PARAM_RANGES.items():
            if key in params:
                resolved[key] = params[key]
            else:
                mid = (lo + hi) / 2.0
                resolved[key] = mid
                log.warning(
                    "trend_continuation.missing_param_using_midpoint",
                    param=key,
                    midpoint=mid,
                )
        super().__init__(resolved)

    async def generate_signals(
        self, candles: dict[str, list]
    ) -> list[CandidateSignal]:
        """Generate trend continuation signals from multi-timeframe candle data.

        Algorithm:
        1. Compute structural EMA(50) and EMA(200) on H1 closes to determine trend.
        2. Compute EMA(pullback_ema) on H1 closes to identify the pullback zone.
        3. Detect pullback touch in last 5 H1 candles.
        4. Detect M15 price action confirmation (engulfing / pin bar / inside bar).
        5. Compute SL, TP1, TP2; compute linear confidence from 3 normalised factors.
        6. Return CandidateSignal list (in-memory only, no DB writes).

        Args:
            candles: Dict keyed by timeframe string ("M15", "H1", "H4", "D1"),
                     values are lists of Candle ORM objects ordered oldest→newest.

        Returns:
            List of CandidateSignal objects.  Returns [] if data is insufficient
            (fewer than 205 H1 candles, ATR=0, or no qualifying pullback/pattern).
        """
        h1_candles: list = candles.get("H1", [])
        m15_candles: list = candles.get("M15", [])

        # --- Guard: minimum H1 data for EMA(200) ---
        if len(h1_candles) < _MIN_H1_CANDLES:
            if self.emit_diagnostic_logs:
                log.debug(
                    "trend_continuation.insufficient_h1_candles",
                    count=len(h1_candles),
                    required=_MIN_H1_CANDLES,
                )
            return []

        if len(m15_candles) < 3:
            if self.emit_diagnostic_logs:
                log.debug("trend_continuation.insufficient_m15_candles", count=len(m15_candles))
            return []

        # --- ATR guard ---
        atr_h1 = self.calculate_atr(h1_candles, period=14)
        if atr_h1 == 0.0:
            if self.emit_diagnostic_logs:
                log.debug("trend_continuation.zero_atr_h1")
            return []

        # --- Compute structural trend EMAs ---
        h1_closes = [float(c.close) for c in h1_candles]
        ema50 = self._ema(h1_closes, _EMA_FAST_STRUCTURAL)
        ema200 = self._ema(h1_closes, _EMA_SLOW_STRUCTURAL)

        ema50_last = float(ema50[-1])
        ema200_last = float(ema200[-1])

        if ema50_last == ema200_last:
            if self.emit_diagnostic_logs:
                log.debug("trend_continuation.no_trend_ema_equal")
            return []

        trend_direction = Direction.BUY if ema50_last > ema200_last else Direction.SELL

        # --- Resolve params ---
        pullback_ema_period = int(round(float(self.params["pullback_ema"])))
        sl_atr_mult = float(self.params["sl_atr_mult"])
        tp_risk_mult = float(self.params["tp_risk_mult"])

        # --- Compute pullback EMA ---
        ema_pb = self._ema(h1_closes, pullback_ema_period)
        ema_pb_last = float(ema_pb[-1])

        # --- Detect pullback touch in last 5 H1 candles ---
        lookback_h1 = h1_candles[-5:]
        pullback_candle, pullback_low, pullback_high = self._detect_pullback(
            lookback_h1, ema_pb_last, trend_direction, atr_h1
        )

        if pullback_candle is None:
            if self.emit_diagnostic_logs:
                log.debug(
                    "trend_continuation.no_pullback_detected",
                    direction=trend_direction.value,
                    ema_pb=ema_pb_last,
                )
            return []

        # --- Detect M15 price action confirmation ---
        m15_recent = m15_candles[-4:]  # last 4 M15 candles
        pattern_type, confirmation_candle = self._detect_pa_pattern(
            m15_recent, trend_direction
        )

        if pattern_type is None or confirmation_candle is None:
            if self.emit_diagnostic_logs:
                log.debug("trend_continuation.no_m15_confirmation", direction=trend_direction.value)
            return []

        # --- Compute SL, TP1, TP2 ---
        entry = float(confirmation_candle.close)

        if trend_direction == Direction.BUY:
            sl = pullback_low - sl_atr_mult * atr_h1
            risk = abs(entry - sl)
            if risk == 0.0:
                return []
            tp1 = entry + tp_risk_mult * risk
            tp2 = self._find_tp2_buy(h1_candles, tp1)
        else:
            sl = pullback_high + sl_atr_mult * atr_h1
            risk = abs(sl - entry)
            if risk == 0.0:
                return []
            tp1 = entry - tp_risk_mult * risk
            tp2 = self._find_tp2_sell(h1_candles, tp1)

        # --- Compute ADX for confidence ---
        adx_value = self._compute_adx(h1_candles, period=14)
        trend_strength_norm = min(adx_value / 50.0, 1.0)

        # --- Pullback quality: how close the touch was to the EMA ---
        if trend_direction == Direction.BUY:
            touch_price = pullback_low
        else:
            touch_price = pullback_high
        gap = abs(touch_price - ema_pb_last)
        pullback_quality_norm = max(0.0, 1.0 - min(gap / max(atr_h1, 1e-9), 1.0))

        # --- PA pattern score ---
        pa_scores = {"engulfing": 1.0, "pin_bar": 0.7, "inside_bar": 0.5}
        pa_score = pa_scores.get(pattern_type, 0.5)

        # --- Linear confidence (D-01), then clamp (D-03) ---
        confidence = (
            W_TREND_STRENGTH * trend_strength_norm
            + W_PULLBACK_QUALITY * pullback_quality_norm
            + W_PA_PATTERN * pa_score
        )
        confidence = float(max(0.0, min(confidence, 1.0)))

        if self.emit_signal_logs:
            log.info(
                "trend_continuation.signal_generated",
                direction=trend_direction.value,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                confidence=confidence,
                pattern=pattern_type,
                adx=adx_value,
            )

        return [
            CandidateSignal(
                strategy=StrategyName.TREND_CONTINUATION,
                direction=trend_direction,
                entry_price=float(entry),
                sl_price=float(sl),
                tp1_price=float(tp1),
                tp2_price=float(tp2),
                confidence=confidence,
                timeframe=Timeframe.H1,
                params_snapshot=self.params,
            )
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ema(self, values: list[float], period: int) -> np.ndarray:
        """Compute EMA using Wilder-style seeding with SMA of first `period` values.

        Args:
            values: List of price values ordered oldest→newest.
            period: EMA lookback period.

        Returns:
            numpy array of EMA values, same length as `values`.
            If fewer values than `period`, returns array filled with the mean.
        """
        return np.array(ema(values, period), dtype=float)

    def _compute_adx(self, candles: list, period: int = 14) -> float:
        """Compute ADX(period) using Wilder-smoothed directional movement.

        ADX quantifies trend strength regardless of direction. Returns 0.0 if
        insufficient data (< 2×period candles).

        Args:
            candles: List of Candle ORM objects, ordered oldest→newest.
            period: ADX lookback period. Defaults to 14.

        Returns:
            ADX value as float in [0.0, 100.0]. Returns 0.0 if < 2×period candles.
        """
        if len(candles) < 2 * period:
            return 0.0

        highs = np.array([float(c.high) for c in candles])
        lows = np.array([float(c.low) for c in candles])
        closes = np.array([float(c.close) for c in candles])

        # Compute TR, +DM, -DM for each period (from index 1 onward)
        n = len(candles)
        tr_arr = np.empty(n - 1)
        plus_dm = np.empty(n - 1)
        minus_dm = np.empty(n - 1)

        for i in range(1, n):
            prev_high = highs[i - 1]
            prev_low = lows[i - 1]
            prev_close = closes[i - 1]
            cur_high = highs[i]
            cur_low = lows[i]

            # True Range
            tr_arr[i - 1] = max(
                cur_high - cur_low,
                abs(cur_high - prev_close),
                abs(cur_low - prev_close),
            )

            # Directional Movement
            up_move = cur_high - prev_high
            down_move = prev_low - cur_low

            if up_move > down_move and up_move > 0:
                plus_dm[i - 1] = up_move
            else:
                plus_dm[i - 1] = 0.0

            if down_move > up_move and down_move > 0:
                minus_dm[i - 1] = down_move
            else:
                minus_dm[i - 1] = 0.0

        # Wilder-smooth TR, +DM, -DM (alpha = 1/period)
        alpha = 1.0 / period

        atr_s = float(np.mean(tr_arr[:period]))
        plus_dm_s = float(np.mean(plus_dm[:period]))
        minus_dm_s = float(np.mean(minus_dm[:period]))

        dx_values: list[float] = []

        for i in range(period, len(tr_arr)):
            atr_s = alpha * tr_arr[i] + (1.0 - alpha) * atr_s
            plus_dm_s = alpha * plus_dm[i] + (1.0 - alpha) * plus_dm_s
            minus_dm_s = alpha * minus_dm[i] + (1.0 - alpha) * minus_dm_s

            if atr_s == 0.0:
                dx_values.append(0.0)
                continue

            di_plus = 100.0 * plus_dm_s / atr_s
            di_minus = 100.0 * minus_dm_s / atr_s
            di_sum = di_plus + di_minus

            if di_sum == 0.0:
                dx_values.append(0.0)
            else:
                dx = 100.0 * abs(di_plus - di_minus) / di_sum
                dx_values.append(dx)

        if not dx_values:
            return 0.0

        # Wilder-smooth DX → ADX
        adx = float(np.mean(dx_values[:period])) if len(dx_values) >= period else float(np.mean(dx_values))
        alpha_adx = 1.0 / period
        start = period if len(dx_values) >= period else 0
        for val in dx_values[start:]:
            adx = alpha_adx * val + (1.0 - alpha_adx) * adx

        return float(adx)

    def _detect_pullback(
        self,
        h1_lookback: list,
        ema_pb: float,
        direction: Direction,
        atr: float,
    ) -> tuple[object | None, float, float]:
        """Detect if price touched the pullback EMA from the correct side in H1 candles.

        Args:
            h1_lookback: Last 5 H1 candles, ordered oldest→newest.
            ema_pb: Current pullback EMA value.
            direction: Trade direction (BUY or SELL).
            atr: ATR14 on H1 for proximity check.

        Returns:
            Tuple of (pullback_candle, pullback_low, pullback_high):
            - pullback_candle: The candle that touched the EMA (or None).
            - pullback_low: Lowest low of the lookback window (for BUY SL).
            - pullback_high: Highest high of the lookback window (for SELL SL).
        """
        if not h1_lookback:
            return None, 0.0, 0.0

        pullback_low = min(float(c.low) for c in h1_lookback)
        pullback_high = max(float(c.high) for c in h1_lookback)

        for candle in h1_lookback:
            low = float(candle.low)
            high = float(candle.high)
            close = float(candle.close)

            if direction == Direction.BUY:
                # Price touched EMA from above: low <= ema_pb or close within 0.1×ATR
                if low <= ema_pb or abs(close - ema_pb) <= 0.1 * atr:
                    return candle, pullback_low, pullback_high
            else:
                # Price touched EMA from below: high >= ema_pb or close within 0.1×ATR
                if high >= ema_pb or abs(close - ema_pb) <= 0.1 * atr:
                    return candle, pullback_low, pullback_high

        return None, pullback_low, pullback_high

    def _detect_pa_pattern(
        self,
        m15_candles: list,
        direction: Direction,
    ) -> tuple[str | None, object | None]:
        """Detect price action confirmation pattern on M15.

        Checks the most recent M15 candle against the prior candle for:
        - Engulfing: current close engulfs prior candle body in trend direction.
        - Pin bar: wick > 2× body with close near trend-direction end.
        - Inside bar breakout: current candle breaks out of prior narrow range.

        Args:
            m15_candles: Last 4 M15 candles, ordered oldest→newest.
            direction: Trade direction (BUY or SELL).

        Returns:
            Tuple of (pattern_type, confirmation_candle):
            - pattern_type: "engulfing", "pin_bar", "inside_bar", or None.
            - confirmation_candle: The confirming M15 candle, or None.
        """
        if len(m15_candles) < 2:
            return None, None

        curr = m15_candles[-1]
        prev = m15_candles[-2]

        curr_open = float(curr.open)
        curr_close = float(curr.close)
        curr_high = float(curr.high)
        curr_low = float(curr.low)
        prev_high = float(prev.high)
        prev_low = float(prev.low)
        prev_open = float(prev.open)
        prev_close = float(prev.close)

        curr_body = abs(curr_close - curr_open)
        curr_range = curr_high - curr_low

        # --- Engulfing ---
        if direction == Direction.BUY:
            if curr_close > prev_high and curr_open < prev_close:
                return "engulfing", curr
        else:
            if curr_close < prev_low and curr_open > prev_close:
                return "engulfing", curr

        # --- Pin bar: total range > 2× body, close near trend-side end ---
        if curr_range > 0 and curr_body < curr_range / 2.0:
            if direction == Direction.BUY:
                # Lower wick dominates; close in upper 40% of range
                close_pct = (curr_close - curr_low) / curr_range
                if close_pct >= 0.6:
                    return "pin_bar", curr
            else:
                # Upper wick dominates; close in lower 40% of range
                close_pct = (curr_close - curr_low) / curr_range
                if close_pct <= 0.4:
                    return "pin_bar", curr

        # --- Inside bar breakout: prev candle range was contained within the candle
        #     before it; current candle breaks out of prev range in trend direction ---
        if len(m15_candles) >= 3:
            ante_prev = m15_candles[-3]
            ante_high = float(ante_prev.high)
            ante_low = float(ante_prev.low)
            # prev is an inside bar if its range is contained within ante_prev
            if prev_high < ante_high and prev_low > ante_low:
                if direction == Direction.BUY and curr_high > prev_high:
                    return "inside_bar", curr
                elif direction == Direction.SELL and curr_low < prev_low:
                    return "inside_bar", curr

        return None, None

    def _find_tp2_buy(self, h1_candles: list, tp1: float) -> float:
        """Find nearest H1 swing high above TP1 for TP2 target.

        Args:
            h1_candles: H1 candles, ordered oldest→newest.
            tp1: TP1 price level (TP2 must be above this).

        Returns:
            Nearest swing high above tp1, or tp1 * 1.5 if none found.
        """
        swing_highs, _ = self.detect_swing_levels(h1_candles, order=10)
        candidates = [h for h in swing_highs if h > tp1]
        if candidates:
            return float(min(candidates))  # nearest swing high above TP1
        return float(tp1 * 1.5)  # fallback

    def _find_tp2_sell(self, h1_candles: list, tp1: float) -> float:
        """Find nearest H1 swing low below TP1 for TP2 target.

        Args:
            h1_candles: H1 candles, ordered oldest→newest.
            tp1: TP1 price level (TP2 must be below this).

        Returns:
            Nearest swing low below tp1, or tp1 * 0.667 if none found.
        """
        _, swing_lows = self.detect_swing_levels(h1_candles, order=10)
        candidates = [lo for lo in swing_lows if lo < tp1]
        if candidates:
            return float(max(candidates))  # nearest swing low below TP1
        return float(tp1 * 0.667)  # fallback
