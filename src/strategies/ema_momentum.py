"""EmaMomentumStrategy — EMA crossover on H1 with EMA(50) trend filter.

Per CLAUDE.md §9.5:
- Entries on clean EMA crossovers confirmed by momentum and trend alignment.
- 3 optimizable parameters: fast_ema, slow_ema, sl_atr_mult.
- Fixed TP = 1.5× risk (not optimized).
- EMA(50) is a STRUCTURAL trend filter — not in PARAM_RANGES.
- No MACD (explicitly forbidden per CLAUDE.md §17 — redundant with EMAs).
- Pure signal generator — no DB writes, no APScheduler references (D-07).
"""

from __future__ import annotations

import numpy as np
import structlog

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    StrategyName,
    Timeframe,
)
from src.strategies.base import AbstractStrategy

log = structlog.get_logger(__name__)

# Structural constant — EMA(50) is the trend filter, never optimized (CLAUDE.md §17).
_EMA50_PERIOD: int = 50

# Fixed TP ratio — 1.5× risk per CLAUDE.md §9.5, not a parameter.
_TP_RISK_RATIO: float = 1.5


class EmaMomentumStrategy(AbstractStrategy):
    """Detect H1 EMA crossovers filtered by EMA(50) trend alignment.

    Signals only when fast EMA crosses slow EMA AND the fast EMA is on the
    correct side of EMA(50), confirming trend alignment.

    Confidence weights (D-01: linear weighted sum, no sigmoid/exp/log/MACD):
        W_CROSSOVER_ANGLE = 0.40 — separation between EMAs shows momentum strength.
            Normalized: |ema_fast - ema_slow| / ATR14(H1), capped at 1.0.
        W_EMA50_DISTANCE  = 0.35 — distance from EMA50 shows trend conviction.
            Normalized: |ema_fast - ema50| / ATR14(H1), capped at 1.0.
        W_TREND_ALIGNMENT = 0.25 — categorical: price on correct side of EMA50.
            1.0 if close > ema50 for BUY (or close < ema50 for SELL), else 0.5.
        Weights sum: 0.40 + 0.35 + 0.25 = 1.00

    Attributes:
        STRATEGY_NAME: Canonical name for StrategyRunner DB query.
        PARAM_RANGES: Optimizable parameters with (min, max) bounds.
    """

    STRATEGY_NAME: str = "ema_momentum"

    PARAM_RANGES: dict[str, tuple[float, float]] = {
        "fast_ema":    (5.0, 15.0),   # Fast EMA period for crossover detection
        "slow_ema":    (15.0, 30.0),  # Slow EMA period for crossover detection
        "sl_atr_mult": (0.2, 0.8),    # SL distance from EMA(slow) (× ATR14)
    }

    # Confidence weight constants — must sum to 1.00 (D-01).
    W_CROSSOVER_ANGLE: float = 0.40
    W_EMA50_DISTANCE: float = 0.35
    W_TREND_ALIGNMENT: float = 0.25

    def _ema(self, values: list[float], period: int) -> np.ndarray:
        """Compute exponential moving average seeded with SMA of first `period` values.

        Alpha = 2 / (period + 1), Wilder-style seed using SMA of the first
        `period` data points, then exponential smoothing for the remainder.

        Note: This helper is duplicated (not imported from other strategy files)
        to keep strategies decoupled per Phase 3 design decisions.

        Args:
            values: List of float price values, ordered oldest → newest.
            period: EMA lookback period.

        Returns:
            Numpy array of EMA values, same length as `values`.
            Returns an array of NaN if fewer values than `period` are provided.
        """
        n = len(values)
        result = np.full(n, np.nan)
        if n < period:
            return result

        alpha = 2.0 / (period + 1)
        seed = float(np.mean(values[:period]))
        result[period - 1] = seed

        for i in range(period, n):
            result[i] = alpha * values[i] + (1.0 - alpha) * result[i - 1]

        return result

    async def generate_signals(
        self, candles: dict[str, list]
    ) -> list[CandidateSignal]:
        """Generate EMA momentum candidate signals from H1 candle data.

        Requires at least 52 H1 candles to compute EMA(50) and detect a
        crossover in the last candle.

        Args:
            candles: Dict keyed by timeframe string. Must include "H1" list of
                     Candle ORM objects ordered oldest → newest.

        Returns:
            List containing one CandidateSignal if all conditions are met,
            otherwise an empty list.
        """
        h1_candles = candles.get("H1", [])

        fast_period = int(self.params["fast_ema"])
        slow_period = int(self.params["slow_ema"])
        sl_atr_mult = float(self.params["sl_atr_mult"])

        # --- Guard: fast_ema must be strictly less than slow_ema (T-03-09) ---
        if fast_period >= slow_period:
            log.warning(
                "ema_momentum.invalid_params",
                fast_ema=fast_period,
                slow_ema=slow_period,
                reason="fast_ema must be < slow_ema",
            )
            return []

        # --- Guard: minimum H1 candles for EMA(50) + crossover detection ---
        # Need EMA50_PERIOD + 2 candles at minimum (50 for EMA seed + 2 for crossover).
        min_h1_required = _EMA50_PERIOD + 2
        if len(h1_candles) < min_h1_required:
            if self.emit_diagnostic_logs:
                log.debug(
                    "ema_momentum.insufficient_h1_candles",
                    have=len(h1_candles),
                    need=min_h1_required,
                )
            return []

        closes = [float(c.close) for c in h1_candles]

        # --- Step 1: Compute EMAs ---
        ema_fast = self._ema(closes, fast_period)
        ema_slow = self._ema(closes, slow_period)
        ema50 = self._ema(closes, _EMA50_PERIOD)

        # Verify all EMAs have valid values at the last two positions.
        for name, arr in (("ema_fast", ema_fast), ("ema_slow", ema_slow), ("ema50", ema50)):
            if np.isnan(arr[-1]) or np.isnan(arr[-2]):
                if self.emit_diagnostic_logs:
                    log.debug("ema_momentum.ema_nan", ema=name)
                return []

        # --- Step 2: Crossover detection (last 2 candles) ---
        direction: Direction | None = None

        if ema_fast[-2] <= ema_slow[-2] and ema_fast[-1] > ema_slow[-1]:
            direction = Direction.BUY
        elif ema_fast[-2] >= ema_slow[-2] and ema_fast[-1] < ema_slow[-1]:
            direction = Direction.SELL

        if direction is None:
            if self.emit_diagnostic_logs:
                log.debug("ema_momentum.no_crossover_detected")
            return []

        # --- Step 3: Trend filter — EMA(fast_ema) vs EMA(50) ---
        if direction == Direction.BUY and ema_fast[-1] <= ema50[-1]:
            if self.emit_diagnostic_logs:
                log.debug(
                    "ema_momentum.trend_filter_failed",
                    direction="BUY",
                    ema_fast=float(ema_fast[-1]),
                    ema50=float(ema50[-1]),
                )
            return []

        if direction == Direction.SELL and ema_fast[-1] >= ema50[-1]:
            if self.emit_diagnostic_logs:
                log.debug(
                    "ema_momentum.trend_filter_failed",
                    direction="SELL",
                    ema_fast=float(ema_fast[-1]),
                    ema50=float(ema50[-1]),
                )
            return []

        # --- Step 4: Entry price ---
        entry = float(h1_candles[-1].close)

        # --- Step 5: SL — opposite side of EMA(slow) adjusted by ATR ---
        atr_h1 = self.calculate_atr(h1_candles, period=14)
        if atr_h1 == 0.0:
            if self.emit_diagnostic_logs:
                log.debug("ema_momentum.atr_zero", timeframe="H1")
            return []

        if direction == Direction.BUY:
            sl = float(ema_slow[-1]) - sl_atr_mult * atr_h1
        else:
            sl = float(ema_slow[-1]) + sl_atr_mult * atr_h1

        # --- Step 6: TP1 — FIXED 1.5× risk (CLAUDE.md §9.5, not parameterized) ---
        risk = abs(entry - sl)
        if direction == Direction.BUY:
            tp1 = entry + _TP_RISK_RATIO * risk
        else:
            tp1 = entry - _TP_RISK_RATIO * risk

        # --- Confidence scoring (D-01: linear weighted sum, D-03: clamp) ---
        # w_crossover_angle = 0.40 — EMA separation reflects momentum strength.
        ema_separation = abs(float(ema_fast[-1]) - float(ema_slow[-1]))
        crossover_angle_norm = min(ema_separation / max(atr_h1, 1e-9), 1.0)

        # w_ema50_distance = 0.35 — distance from EMA50 shows trend conviction.
        ema50_gap = abs(float(ema_fast[-1]) - float(ema50[-1]))
        ema50_distance_norm = min(ema50_gap / max(atr_h1, 1e-9), 1.0)

        # w_trend_alignment = 0.25 — price on correct side of EMA50.
        last_close = float(h1_candles[-1].close)
        if direction == Direction.BUY:
            trend_alignment_score = 1.0 if last_close > float(ema50[-1]) else 0.5
        else:
            trend_alignment_score = 1.0 if last_close < float(ema50[-1]) else 0.5

        confidence = (
            self.W_CROSSOVER_ANGLE * crossover_angle_norm
            + self.W_EMA50_DISTANCE * ema50_distance_norm
            + self.W_TREND_ALIGNMENT * trend_alignment_score
        )
        confidence = max(0.0, min(confidence, 1.0))  # D-03: clamp

        signal = CandidateSignal(
            strategy=StrategyName.EMA_MOMENTUM,
            direction=direction,
            entry_price=entry,
            sl_price=sl,
            tp1_price=tp1,
            tp2_price=None,  # EMA Momentum is single-target: 1.5× risk only
            confidence=confidence,
            timeframe=Timeframe.H1,
            params_snapshot=self.params,
        )

        if self.emit_signal_logs:
            log.info(
                "ema_momentum.signal_generated",
                direction=direction.value,
                entry=entry,
                sl=sl,
                tp1=tp1,
                confidence=round(confidence, 4),
                fast_ema=fast_period,
                slow_ema=slow_period,
            )

        return [signal]
