"""Liquidity Sweep strategy — H4 swing level sweep detection with M15 entry signals.

Per CLAUDE.md §9.2 — Detect false breakouts at S/R levels (price sweeps liquidity then reverses).
"""

from __future__ import annotations

import numpy as np
import structlog

from src.models.signal_data import CandidateSignal, Direction, StrategyName, Timeframe
from src.strategies.base import AbstractStrategy

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Confidence weights — must sum to 1.0 (D-01: linear weighted sum, no sigmoid)
# ---------------------------------------------------------------------------
# W_SWEEP_DEPTH = 0.40 — sweep depth quality is the primary signal driver
#   (how far below/above the level price swept, normalized by 2×ATR; capped at 1.0)
# W_VOLUME_SPIKE = 0.35 — volume expansion confirms institutional activity
#   (sweep-candle volume vs 20-period SMA volume; normalized to [0, 1], capped at 1.0)
# W_PROXIMITY = 0.25 — proximity to a cluster of major levels increases reliability
#   (count of swing levels within 1.0×ATR of the swept level, normalized by 3)
# Weights sum = 0.40 + 0.35 + 0.25 = 1.00
W_SWEEP_DEPTH = 0.40
W_VOLUME_SPIKE = 0.35
W_PROXIMITY = 0.25


class LiquiditySweepStrategy(AbstractStrategy):
    """Detect liquidity sweeps at H4 swing highs/lows and generate M15 reversal entries.

    Concept: Identify S/R levels on H4, find M15 candles whose wick pierces a level
    (sweep) by more than sweep_atr_mult×ATR14 but whose close returns inside the level,
    then enter in the reversal direction.

    PARAM_RANGES contains exactly 3 optimisable parameters per CLAUDE.md §17:
    - sweep_atr_mult: minimum sweep depth filter (0.2–0.8 × ATR14)
    - sl_atr_mult:    SL distance beyond sweep wick (0.3–1.0 × ATR14)
    - tp_risk_mult:   TP1 as multiple of entry→SL risk (1.2–3.0×)

    Fixed structural parameters (never optimised, per CLAUDE.md §17):
    - Swing detection order=10 on H4 (argrelextrema structural param)
    - TP2 = 2 × TP1 distance from entry (fixed ratio)

    Attributes:
        STRATEGY_NAME: Canonical strategy identifier used by StrategyRunner DB queries.
        PARAM_RANGES: Dict of optimisable param name → (min, max) range.
    """

    STRATEGY_NAME = "liquidity_sweep"

    PARAM_RANGES: dict[str, tuple[float, float]] = {
        "sweep_atr_mult": (0.2, 0.8),   # Minimum sweep depth filter (×ATR14)
        "sl_atr_mult":    (0.3, 1.0),   # SL distance beyond sweep wick (×ATR14)
        "tp_risk_mult":   (1.2, 3.0),   # TP1 as multiple of entry→SL risk
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
                    "liquidity_sweep.missing_param_using_midpoint",
                    param=key,
                    midpoint=mid,
                )
        super().__init__(resolved)

    async def generate_signals(
        self, candles: dict[str, list]
    ) -> list[CandidateSignal]:
        """Generate liquidity sweep signals from multi-timeframe candle data.

        Algorithm:
        1. Detect swing highs/lows on H4 candles (order=10) — these are S/R levels.
        2. Also aggregate D1 levels if available.
        3. For each M15 candle (most recent 50): check if wick pierces a level by
           > sweep_atr_mult×ATR14(M15) with close returning inside.
        4. Take the most recent qualifying sweep candle.
        5. Compute SL, TP1, TP2; compute linear confidence from 3 normalised factors.
        6. Return CandidateSignal list (in-memory only, no DB writes).

        Args:
            candles: Dict keyed by timeframe string ("M15", "H1", "H4", "D1"),
                     values are lists of Candle ORM objects ordered oldest→newest.

        Returns:
            List of CandidateSignal objects.  Returns [] if data is insufficient
            (ATR=0, or fewer than 21 H4 candles for swing detection).
        """
        m15_candles: list = candles.get("M15", [])
        h4_candles: list = candles.get("H4", [])
        d1_candles: list = candles.get("D1", [])

        # --- guard: minimum H4 data for swing detection (order=10 needs 2*10+1 = 21) ---
        if len(h4_candles) < 21:
            log.debug(
                "liquidity_sweep.insufficient_h4_candles",
                count=len(h4_candles),
                required=21,
            )
            return []

        if len(m15_candles) < 2:
            log.debug("liquidity_sweep.insufficient_m15_candles", count=len(m15_candles))
            return []

        # --- ATR guard ---
        atr_m15 = self.calculate_atr(m15_candles, period=14)
        if atr_m15 == 0.0:
            log.debug("liquidity_sweep.zero_atr_m15")
            return []

        # --- Collect S/R levels from H4 (and optionally D1) ---
        h4_highs, h4_lows = self.detect_swing_levels(h4_candles, order=10)
        all_swing_levels: list[float] = h4_highs + h4_lows

        if len(d1_candles) >= 21:
            d1_highs, d1_lows = self.detect_swing_levels(d1_candles, order=10)
            all_swing_levels += d1_highs + d1_lows

        if not all_swing_levels:
            log.debug("liquidity_sweep.no_swing_levels_detected")
            return []

        # --- Resolve params ---
        sweep_atr_mult: float = float(self.params["sweep_atr_mult"])
        sl_atr_mult: float = float(self.params["sl_atr_mult"])
        tp_risk_mult: float = float(self.params["tp_risk_mult"])

        # --- Volume SMA over last 20 M15 candles ---
        vol_window = m15_candles[-20:] if len(m15_candles) >= 20 else m15_candles
        sma_vol: float = float(
            np.mean([float(c.volume) for c in vol_window]) if vol_window else 1.0
        )

        # --- Scan most recent 50 M15 candles for sweeps (newest first) ---
        scan_candles = m15_candles[-50:]
        signals: list[CandidateSignal] = []

        for i in range(len(scan_candles) - 1, -1, -1):
            candle = scan_candles[i]
            low = float(candle.low)
            high = float(candle.high)
            close = float(candle.close)
            volume = float(candle.volume)

            for level in all_swing_levels:
                # --- BUY setup: wick sweeps below support, close returns above ---
                if low < level - sweep_atr_mult * atr_m15 and close > level:
                    sweep_depth = level - low  # how far below level wick went
                    direction = Direction.BUY

                    # SL: below the sweep wick (and level), padded by sl_atr_mult×ATR
                    sl = min(low, level) - sl_atr_mult * atr_m15
                    entry = close
                    risk = abs(entry - sl)
                    if risk == 0.0:
                        continue
                    tp1 = entry + tp_risk_mult * risk
                    tp2 = entry + 2.0 * (tp1 - entry)  # TP2 = 2× TP1 distance

                    confidence = self._compute_confidence(
                        sweep_depth=sweep_depth,
                        atr=atr_m15,
                        volume=volume,
                        sma_vol=sma_vol,
                        level=level,
                        all_levels=all_swing_levels,
                        has_volume=True,
                    )

                    log.info(
                        "liquidity_sweep.signal_generated",
                        direction="BUY",
                        entry=entry,
                        sl=sl,
                        tp1=tp1,
                        tp2=tp2,
                        confidence=confidence,
                        sweep_depth=sweep_depth,
                        level=level,
                    )

                    signals.append(
                        CandidateSignal(
                            strategy=StrategyName.LIQUIDITY_SWEEP,
                            direction=direction,
                            entry_price=float(entry),
                            sl_price=float(sl),
                            tp1_price=float(tp1),
                            tp2_price=float(tp2),
                            confidence=confidence,
                            timeframe=Timeframe.M15,
                            params_snapshot=self.params,
                        )
                    )
                    # Take only the most recent qualifying sweep candle
                    break

                # --- SELL setup: wick sweeps above resistance, close returns below ---
                elif high > level + sweep_atr_mult * atr_m15 and close < level:
                    sweep_depth = high - level  # how far above level wick went
                    direction = Direction.SELL

                    # SL: above the sweep wick (and level), padded by sl_atr_mult×ATR
                    sl = max(high, level) + sl_atr_mult * atr_m15
                    entry = close
                    risk = abs(sl - entry)
                    if risk == 0.0:
                        continue
                    tp1 = entry - tp_risk_mult * risk
                    tp2 = entry - 2.0 * (entry - tp1)  # TP2 = 2× TP1 distance

                    confidence = self._compute_confidence(
                        sweep_depth=sweep_depth,
                        atr=atr_m15,
                        volume=volume,
                        sma_vol=sma_vol,
                        level=level,
                        all_levels=all_swing_levels,
                        has_volume=True,
                    )

                    log.info(
                        "liquidity_sweep.signal_generated",
                        direction="SELL",
                        entry=entry,
                        sl=sl,
                        tp1=tp1,
                        tp2=tp2,
                        confidence=confidence,
                        sweep_depth=sweep_depth,
                        level=level,
                    )

                    signals.append(
                        CandidateSignal(
                            strategy=StrategyName.LIQUIDITY_SWEEP,
                            direction=direction,
                            entry_price=float(entry),
                            sl_price=float(sl),
                            tp1_price=float(tp1),
                            tp2_price=float(tp2),
                            confidence=confidence,
                            timeframe=Timeframe.M15,
                            params_snapshot=self.params,
                        )
                    )
                    break

            # Stop after first (most-recent) qualifying sweep candle
            if signals:
                break

        return signals

    def _compute_confidence(
        self,
        sweep_depth: float,
        atr: float,
        volume: float,
        sma_vol: float,
        level: float,
        all_levels: list[float],
        has_volume: bool,
    ) -> float:
        """Compute linear weighted confidence score for a sweep signal.

        Confidence = W_SWEEP_DEPTH × depth_norm
                   + W_VOLUME_SPIKE × vol_norm
                   + W_PROXIMITY × proximity_norm
        All components are in [0.0, 1.0]; result clamped to [0.0, 1.0] (D-03).

        Args:
            sweep_depth: Price distance the wick penetrated beyond the level.
            atr: ATR14 value for the M15 timeframe.
            volume: Volume of the sweep candle.
            sma_vol: 20-period SMA of M15 volume.
            level: The S/R level that was swept.
            all_levels: All detected swing levels (for proximity count).
            has_volume: Whether valid volume data exists (always True here).

        Returns:
            Confidence score in [0.0, 1.0].
        """
        # Factor 1: Sweep depth — how deep the wick went relative to ATR
        # Normalise: depth / (2 × ATR), capped at 1.0
        sweep_depth_norm = min(sweep_depth / (2.0 * atr), 1.0) if atr > 0 else 0.0

        # Factor 2: Volume spike — candle volume vs SMA volume
        # Normalise: (vol/sma_vol - 1), capped at [0, 1]. Fallback 0.5 if no vol data.
        if has_volume and sma_vol > 0:
            vol_ratio = volume / max(sma_vol, 1.0) - 1.0
            volume_spike_norm = max(0.0, min(vol_ratio, 1.0))
        else:
            volume_spike_norm = 0.5  # neutral fallback

        # Factor 3: Proximity — how many other swing levels are near this one
        # Count levels within 1.0×ATR; normalise by 3 (max expected cluster size)
        proximity_count = sum(1 for lvl in all_levels if abs(lvl - level) < atr)
        proximity_norm = min(proximity_count / 3.0, 1.0)

        # Linear weighted sum (D-01) then clamp (D-03)
        confidence = (
            W_SWEEP_DEPTH * sweep_depth_norm
            + W_VOLUME_SPIKE * volume_spike_norm
            + W_PROXIMITY * proximity_norm
        )
        return float(max(0.0, min(confidence, 1.0)))
