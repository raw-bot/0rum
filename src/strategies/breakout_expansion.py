"""BreakoutExpansionStrategy — H4 range breakout with H1 volume confirmation.

Per CLAUDE.md §9.4:
- Captures explosive moves out of consolidation ranges.
- 3 optimizable parameters: squeeze_lookback, volume_mult, sl_atr_mult.
- Fixed TP = 1× range width (not optimized).
- No RSI (explicitly excluded per CLAUDE.md §17).
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


class BreakoutExpansionStrategy(AbstractStrategy):
    """Detect H4 consolidation range breakouts confirmed by H1 volume surge.

    Identifies periods where H4 price range is compressed (< 1.5 × ATR14),
    then triggers on an H1 close that breaches the range boundary with above-
    average volume.

    Confidence weights (D-01: linear weighted sum, no sigmoid/exp/log):
        W_SQUEEZE_DURATION = 0.35 — longer squeeze compresses more energy.
            Normalized: actual_lookback_count / 30.0, capped at 1.0.
        W_VOLUME_RATIO     = 0.45 — volume confirmation is the primary signal.
            Normalized: (vol/sma20 - 1.0) / (2.5 - 1.0), capped at [0, 1].
            Uses max expected multiplier 2.5 from PARAM_RANGES upper bound.
        W_RANGE_CLARITY    = 0.20 — tighter range → cleaner breakout level.
            Normalized: 1.0 - range_width / (1.5 × ATR14), capped at [0, 1].
        Weights sum: 0.35 + 0.45 + 0.20 = 1.00

    Attributes:
        STRATEGY_NAME: Canonical name for StrategyRunner DB query.
        PARAM_RANGES: Optimizable parameters with (min, max) bounds.
    """

    STRATEGY_NAME: str = "breakout_expansion"

    PARAM_RANGES: dict[str, tuple[float, float]] = {
        "squeeze_lookback": (12.0, 30.0),  # Min H4 consolidation candles to qualify
        "volume_mult":      (1.2, 2.5),    # Volume threshold at breakout (× SMA20 volume)
        "sl_atr_mult":      (0.5, 1.5),    # SL distance from range midpoint (× ATR14)
    }

    # Confidence weight constants — must sum to 1.00 (D-01).
    W_SQUEEZE_DURATION: float = 0.35
    W_VOLUME_RATIO: float = 0.45
    W_RANGE_CLARITY: float = 0.20

    async def generate_signals(
        self, candles: dict[str, list]
    ) -> list[CandidateSignal]:
        """Generate breakout expansion candidate signals from multi-timeframe data.

        Requires H4 candles for range detection and ATR computation, and H1
        candles for breakout and volume confirmation.

        Args:
            candles: Dict keyed by timeframe string. Must include "H4" and "H1"
                     lists of Candle ORM objects ordered oldest → newest.

        Returns:
            List containing one CandidateSignal if all conditions are met,
            otherwise an empty list.
        """
        h4_candles = candles.get("H4", [])
        h1_candles = candles.get("H1", [])

        squeeze_lookback = int(self.params["squeeze_lookback"])
        volume_mult = float(self.params["volume_mult"])
        sl_atr_mult = float(self.params["sl_atr_mult"])

        # --- Guard: minimum H4 candles needed (squeeze window + ATR period) ---
        min_h4_required = squeeze_lookback + 14
        if len(h4_candles) < min_h4_required:
            if self.emit_diagnostic_logs:
                log.debug(
                    "breakout_expansion.insufficient_h4_candles",
                    have=len(h4_candles),
                    need=min_h4_required,
                )
            return []

        # --- Guard: minimum H1 candles needed (20 for SMA + at least 1 breakout) ---
        if len(h1_candles) < 21:
            if self.emit_diagnostic_logs:
                log.debug(
                    "breakout_expansion.insufficient_h1_candles",
                    have=len(h1_candles),
                    need=21,
                )
            return []

        # --- Step 1: ATR(14) on H4 ---
        atr_h4 = self.calculate_atr(h4_candles, period=14)
        if atr_h4 == 0.0:
            if self.emit_diagnostic_logs:
                log.debug("breakout_expansion.atr_zero", timeframe="H4")
            return []

        # --- Step 2: Range qualification — last squeeze_lookback H4 candles ---
        squeeze_window = h4_candles[-squeeze_lookback:]
        range_high = float(max(float(c.high) for c in squeeze_window))
        range_low = float(min(float(c.low) for c in squeeze_window))
        range_width = range_high - range_low

        if range_width >= 1.5 * atr_h4:
            if self.emit_diagnostic_logs:
                log.debug(
                    "breakout_expansion.range_too_wide",
                    range_width=range_width,
                    threshold=1.5 * atr_h4,
                )
            return []

        # --- Step 3: Breakout check — last 2 H1 candles ---
        breakout_candle = None
        direction: Direction | None = None

        for candidate in reversed(h1_candles[-2:]):
            close = float(candidate.close)
            if close > range_high:
                breakout_candle = candidate
                direction = Direction.BUY
                break
            if close < range_low:
                breakout_candle = candidate
                direction = Direction.SELL
                break

        if breakout_candle is None or direction is None:
            if self.emit_diagnostic_logs:
                log.debug("breakout_expansion.no_breakout_detected")
            return []

        # --- Step 4: Volume confirmation ---
        # SMA20 over the 20 H1 candles preceding the breakout candle.
        # The breakout candle is within h1_candles[-2:], so take the 20
        # candles before the last 2 to form the reference SMA window.
        vol_reference_slice = h1_candles[-22:-2]
        if len(vol_reference_slice) < 20:
            # Fall back to whatever we have before the last 2 candles.
            vol_reference_slice = h1_candles[:-2]

        if not vol_reference_slice:
            if self.emit_diagnostic_logs:
                log.debug("breakout_expansion.no_volume_reference")
            return []

        sma20_volume = float(
            np.mean([float(c.volume) for c in vol_reference_slice])
        )
        breakout_volume = float(breakout_candle.volume)

        if breakout_volume <= volume_mult * max(sma20_volume, 1.0):
            if self.emit_diagnostic_logs:
                log.debug(
                    "breakout_expansion.volume_insufficient",
                    breakout_volume=breakout_volume,
                    threshold=volume_mult * sma20_volume,
                )
            return []

        # --- Steps 5-8: Entry, SL, TP ---
        entry = float(breakout_candle.close)
        range_mid = (range_high + range_low) / 2.0

        if direction == Direction.BUY:
            sl = range_mid - sl_atr_mult * atr_h4
            tp1 = entry + range_width  # FIXED: 1× range width (CLAUDE.md §9.4)
        else:
            sl = range_mid + sl_atr_mult * atr_h4
            tp1 = entry - range_width  # FIXED: 1× range width (CLAUDE.md §9.4)

        # --- Confidence scoring (D-01: linear weighted sum, D-03: clamp) ---
        # w_squeeze_duration = 0.35 — longer squeeze = more compressed energy.
        squeeze_duration_norm = min(float(squeeze_lookback) / 30.0, 1.0)

        # w_volume_ratio = 0.45 — volume surge is primary breakout quality signal.
        vol_ratio = breakout_volume / max(sma20_volume, 1.0)
        volume_ratio_norm = max(0.0, min((vol_ratio - 1.0) / (2.5 - 1.0), 1.0))

        # w_range_clarity = 0.20 — tighter range → cleaner, more reliable level.
        range_clarity_norm = max(
            0.0, 1.0 - range_width / max(1.5 * atr_h4, 1e-9)
        )

        confidence = (
            self.W_SQUEEZE_DURATION * squeeze_duration_norm
            + self.W_VOLUME_RATIO * volume_ratio_norm
            + self.W_RANGE_CLARITY * range_clarity_norm
        )
        confidence = max(0.0, min(confidence, 1.0))  # D-03: clamp

        signal = CandidateSignal(
            strategy=StrategyName.BREAKOUT_EXPANSION,
            direction=direction,
            entry_price=entry,
            sl_price=sl,
            tp1_price=tp1,
            tp2_price=None,  # No TP2 for Breakout Expansion (spec defines single target)
            confidence=confidence,
            timeframe=Timeframe.H4,
            params_snapshot=self.params,
        )

        if self.emit_signal_logs:
            log.info(
                "breakout_expansion.signal_generated",
                direction=direction.value,
                entry=entry,
                sl=sl,
                tp1=tp1,
                confidence=round(confidence, 4),
                squeeze_lookback=squeeze_lookback,
                range_width=range_width,
            )

        return [signal]
