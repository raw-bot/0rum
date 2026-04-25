"""Abstract base class for all 4 XAUUSD trading strategies.

Per CLAUDE.md §9.1 — all strategy implementations must subclass AbstractStrategy.
"""

from abc import ABC, abstractmethod

import numpy as np
import structlog
from scipy.signal import argrelextrema

from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)


class AbstractStrategy(ABC):
    """Base class for all 4 trading strategies.

    Each subclass must:
    - Define PARAM_RANGES with at most 3 optimisable parameters (CLAUDE.md §17).
    - Define STRATEGY_NAME as a class-level string (used by StrategyRunner DB queries).
    - Implement generate_signals() as an async method returning list[CandidateSignal].

    Shared helpers (calculate_atr, detect_swing_levels) are provided here and must
    not be overridden by subclasses.

    Attributes:
        PARAM_RANGES: Dict of param_name → (min, max) optimisation range.
            Max 3 keys per CLAUDE.md §17 anti-pattern guidance.
        params: Active parameter values for this strategy instance.
    """

    # Each subclass declares its optimisable params (max 3 per CLAUDE.md §17).
    # Keys: param names. Values: (min, max) range for the optimizer.
    PARAM_RANGES: dict[str, tuple[float, float]] = {}

    def __init__(self, params: dict) -> None:
        """Initialise strategy with resolved parameter values.

        Args:
            params: Dict of param_name → value. Either loaded from DB optimizer
                    results or computed as midpoints of PARAM_RANGES by StrategyRunner.
        """
        self.params = params
        # Backtests call generate_signals() hundreds of thousands of times; keep
        # per-signal INFO logs enabled for runtime usage, but allow the optimizer
        # to mute them to avoid overwhelming stdout and slowing execution.
        self.emit_signal_logs = True
        self.emit_diagnostic_logs = True

    @abstractmethod
    async def generate_signals(
        self, candles: dict[str, list]
    ) -> list[CandidateSignal]:
        """Generate candidate signals from multi-timeframe candle data.

        Args:
            candles: Dict keyed by timeframe string ("M15", "H1", "H4", "D1"),
                     value is list of Candle ORM objects ordered oldest→newest.

        Returns:
            List of CandidateSignal Pydantic objects (in-memory only, no DB writes).
        """
        ...

    def calculate_atr(self, candles: list, period: int = 14) -> float:
        """Compute ATR(period) from a list of Candle ORM objects.

        Uses Wilder's smoothing: TR = max(high-low, |high-prev_close|, |low-prev_close|).
        Seeds with SMA of first `period` TR values, then applies exponential smoothing
        with alpha = 1/period (Wilder's original method).

        Falls back to mean(TR) if fewer TRs than period are available.

        Args:
            candles: List of Candle ORM objects, ordered oldest→newest.
            period: ATR lookback period. Defaults to 14 per CLAUDE.md §9.1.

        Returns:
            ATR value as float. Returns 0.0 if candles list is empty.
        """
        if not candles:
            return 0.0

        highs = np.array([float(c.high) for c in candles])
        lows = np.array([float(c.low) for c in candles])
        closes = np.array([float(c.close) for c in candles])

        if len(candles) < 2:
            return float(highs[0] - lows[0])

        prev_closes = closes[:-1]
        cur_highs = highs[1:]
        cur_lows = lows[1:]

        tr = np.maximum(
            cur_highs - cur_lows,
            np.maximum(
                np.abs(cur_highs - prev_closes),
                np.abs(cur_lows - prev_closes),
            ),
        )

        if len(tr) < period:
            return float(np.mean(tr))

        # Wilder's smoothing: seed with SMA of first `period` TRs, then EMA
        atr = float(np.mean(tr[:period]))
        alpha = 1.0 / period
        for val in tr[period:]:
            atr = alpha * float(val) + (1.0 - alpha) * atr
        return atr

    def detect_swing_levels(
        self, candles: list, order: int = 10
    ) -> tuple[list[float], list[float]]:
        """Detect swing highs and lows via scipy.signal.argrelextrema.

        Per CLAUDE.md §17: order=10 is a STRUCTURAL parameter — never optimised.
        The default value of 10 is fixed for all strategies.

        Args:
            candles: List of Candle ORM objects, ordered oldest→newest.
            order: Window half-size for argrelextrema. Defaults to 10 (fixed structural
                   parameter — do not change per CLAUDE.md §17 anti-pattern #2).

        Returns:
            Tuple of (swing_highs, swing_lows) — lists of price floats.
            Returns ([], []) if fewer than 2*order+1 candles are provided
            (insufficient data for argrelextrema to find extrema).
        """
        if len(candles) < 2 * order + 1:
            return [], []

        highs = np.array([float(c.high) for c in candles])
        lows = np.array([float(c.low) for c in candles])

        high_indices = argrelextrema(highs, np.greater, order=order)[0]
        low_indices = argrelextrema(lows, np.less, order=order)[0]

        swing_highs = [float(highs[i]) for i in high_indices]
        swing_lows = [float(lows[i]) for i in low_indices]

        return swing_highs, swing_lows
