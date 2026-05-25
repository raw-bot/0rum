"""Tests for AbstractStrategy base class — ATR and swing detection helpers.

TDD RED phase: These tests define the expected behaviour before implementation.
"""

import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock


def make_candle(high: float, low: float, close: float, open_: float = None) -> MagicMock:
    """Create a Candle ORM-like mock object with OHLC fields."""
    candle = MagicMock()
    candle.high = Decimal(str(high))
    candle.low = Decimal(str(low))
    candle.close = Decimal(str(close))
    candle.open = Decimal(str(open_ if open_ is not None else close))
    return candle


def make_candle_series(n: int = 20) -> list:
    """Generate a series of n simple candles with incrementing OHLC for test determinism."""
    candles = []
    base = 2000.0
    for i in range(n):
        high = base + i * 2.0 + 1.5
        low = base + i * 2.0 - 1.5
        close = base + i * 2.0 + 0.5
        open_ = base + i * 2.0 - 0.5
        candles.append(make_candle(high=high, low=low, close=close, open_=open_))
    return candles


class TestAbstractStrategyInstantiation:
    """Test 1: AbstractStrategy cannot be instantiated directly."""

    def test_abstract_strategy_cannot_be_instantiated(self):
        """Instantiating AbstractStrategy directly must raise TypeError."""
        from src.strategies.base import AbstractStrategy

        with pytest.raises(TypeError):
            AbstractStrategy(params={})

    def test_concrete_subclass_can_be_instantiated(self):
        """A concrete subclass implementing generate_signals can be instantiated."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {"a": (1.0, 2.0)}

            async def generate_signals(self, candles: dict) -> list:
                return []

        instance = ConcreteStrategy(params={"a": 1.5})
        assert instance.params == {"a": 1.5}


class TestCalculateAtr:
    """Test 2: calculate_atr() returns a positive float for known OHLC data."""

    def test_calculate_atr_returns_positive_float(self):
        """ATR(14) must return a positive float for standard candle list."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        strategy = ConcreteStrategy(params={})
        candles = make_candle_series(20)
        atr = strategy.calculate_atr(candles, period=14)

        assert isinstance(atr, float), f"Expected float, got {type(atr)}"
        assert atr > 0.0, f"Expected positive ATR, got {atr}"

    def test_calculate_atr_empty_returns_zero(self):
        """ATR with empty candle list must return 0.0."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        strategy = ConcreteStrategy(params={})
        atr = strategy.calculate_atr([], period=14)
        assert atr == 0.0

    def test_calculate_atr_single_candle_returns_hl_range(self):
        """ATR with single candle returns high - low."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        strategy = ConcreteStrategy(params={})
        candle = make_candle(high=2010.0, low=2000.0, close=2005.0)
        atr = strategy.calculate_atr([candle], period=14)
        assert atr == pytest.approx(10.0)

    def test_calculate_atr_uses_true_range_not_just_hl(self):
        """ATR must use True Range (including prev close gap), not just high-low."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        strategy = ConcreteStrategy(params={})
        # Large gap between candle 1 close and candle 2 high/low
        candle1 = make_candle(high=2010.0, low=2000.0, close=2005.0)
        candle2 = make_candle(high=2030.0, low=2025.0, close=2028.0)  # gap up
        atr = strategy.calculate_atr([candle1, candle2], period=14)
        # TR for candle2: max(2030-2025=5, |2030-2005|=25, |2025-2005|=20) = 25
        assert atr == pytest.approx(25.0)

    def test_atr_is_shared_across_strategy_regime_and_walk_forward(self):
        """ATR implementations must agree on the same known candle sequence."""
        from src.backtesting.regime_detector import RegimeDetector
        from src.backtesting.walk_forward import _calculate_atr as walk_forward_atr
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        candles = []
        close = 2000.0
        for i in range(30):
            high = close + 2.0 + (i % 5) * 0.7
            low = close - 1.0 - (i % 3) * 0.4
            next_close = low + (high - low) * (0.35 + (i % 4) * 0.1)
            candles.append(make_candle(high=high, low=low, close=next_close, open_=close))
            close = next_close + (1.5 if i % 2 == 0 else -0.8)

        strategy_atr = ConcreteStrategy(params={}).calculate_atr(candles, period=14)
        regime_atr = RegimeDetector()._calculate_atr(candles, period=14)
        wf_atr = walk_forward_atr(candles, period=14)

        assert regime_atr == pytest.approx(strategy_atr)
        assert wf_atr == pytest.approx(strategy_atr)


class TestDetectSwingLevels:
    """Test 3: detect_swing_levels() returns (highs, lows) tuple on 100 candles."""

    def test_detect_swing_levels_returns_tuple_of_two_lists(self):
        """detect_swing_levels must return a tuple of (list, list) without error."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        strategy = ConcreteStrategy(params={})
        # Use 100 candles with oscillating prices to create swing points
        candles = []
        for i in range(100):
            # Create oscillating pattern: high at even indexes, low at odd indexes
            if i % 10 == 5:
                high = 2020.0  # local high
                low = 2019.0
            elif i % 10 == 0:
                high = 2001.0
                low = 2000.0  # local low
            else:
                high = 2010.0
                low = 2009.0
            candles.append(make_candle(high=high, low=low, close=(high + low) / 2))

        highs, lows = strategy.detect_swing_levels(candles, order=10)

        assert isinstance(highs, list), f"Expected list for highs, got {type(highs)}"
        assert isinstance(lows, list), f"Expected list for lows, got {type(lows)}"

    def test_detect_swing_levels_too_few_candles_returns_empty(self):
        """detect_swing_levels with fewer than 2*order+1 candles returns ([], [])."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        strategy = ConcreteStrategy(params={})
        candles = make_candle_series(5)  # less than 2*10+1=21
        highs, lows = strategy.detect_swing_levels(candles, order=10)

        assert highs == []
        assert lows == []

    def test_detect_swing_levels_values_are_floats(self):
        """All returned swing levels must be float values."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {}

            async def generate_signals(self, candles: dict) -> list:
                return []

        strategy = ConcreteStrategy(params={})
        candles = make_candle_series(100)
        highs, lows = strategy.detect_swing_levels(candles, order=5)

        for h in highs:
            assert isinstance(h, float), f"Expected float swing high, got {type(h)}"
        for l in lows:
            assert isinstance(l, float), f"Expected float swing low, got {type(l)}"


class TestParamRanges:
    """Test 4: Concrete subclass with PARAM_RANGES works correctly."""

    def test_subclass_with_param_ranges_passes_instantiation(self):
        """Subclass with PARAM_RANGES = {'a': (1.0, 2.0)} instantiates correctly."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {"a": (1.0, 2.0)}

            async def generate_signals(self, candles: dict) -> list:
                return []

        instance = ConcreteStrategy(params={"a": 1.5})
        assert instance.PARAM_RANGES == {"a": (1.0, 2.0)}

    def test_subclass_midpoint_calculation(self):
        """Midpoint of PARAM_RANGES {'a': (1.0, 2.0)} is 1.5."""
        from src.strategies.base import AbstractStrategy

        class ConcreteStrategy(AbstractStrategy):
            PARAM_RANGES = {"a": (1.0, 2.0)}

            async def generate_signals(self, candles: dict) -> list:
                return []

        midpoints = {
            param: (lo + hi) / 2.0
            for param, (lo, hi) in ConcreteStrategy.PARAM_RANGES.items()
        }
        assert midpoints == {"a": 1.5}

    def test_three_param_subclass_works_correctly(self):
        """Concrete subclass with 3 PARAM_RANGES works and all midpoints correct."""
        from src.strategies.base import AbstractStrategy

        class LiquiditySweepStrategy(AbstractStrategy):
            PARAM_RANGES = {
                "sweep_atr_mult": (0.2, 0.8),
                "sl_atr_mult": (0.3, 1.0),
                "tp_risk_mult": (1.2, 3.0),
            }

            async def generate_signals(self, candles: dict) -> list:
                return []

        instance = LiquiditySweepStrategy(
            params={
                "sweep_atr_mult": 0.5,
                "sl_atr_mult": 0.65,
                "tp_risk_mult": 2.1,
            }
        )
        assert instance.params["sweep_atr_mult"] == 0.5
        midpoints = {
            param: (lo + hi) / 2.0
            for param, (lo, hi) in LiquiditySweepStrategy.PARAM_RANGES.items()
        }
        assert midpoints["sweep_atr_mult"] == pytest.approx(0.5)
        assert midpoints["sl_atr_mult"] == pytest.approx(0.65)
        assert midpoints["tp_risk_mult"] == pytest.approx(2.1)
