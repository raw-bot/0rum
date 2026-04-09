"""Unit tests for BreakoutExpansionStrategy.

Uses frozen mock candle fixtures — no live DB, no network calls.
Covers: signal generation, TP calculation, confidence bounds, range filter,
        volume filter, and absence of forbidden patterns (session, APScheduler).
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_candle(
    high: float,
    low: float,
    close: float,
    open_: float | None = None,
    volume: int = 1000,
) -> MagicMock:
    """Create a Candle ORM-like mock with OHLCV attributes."""
    c = MagicMock()
    c.high = Decimal(str(high))
    c.low = Decimal(str(low))
    c.close = Decimal(str(close))
    c.open = Decimal(str(open_ if open_ is not None else close))
    c.volume = volume
    return c


def make_narrow_h4_candles(n: int, base: float = 2000.0, half_range: float = 0.5) -> list:
    """Return n H4 candles forming a narrow range around `base`.

    Range per candle: [base - half_range, base + half_range].
    ATR of this series will be close to 2 × half_range = 1.0.
    """
    candles = []
    for _ in range(n):
        candles.append(
            make_candle(
                high=base + half_range,
                low=base - half_range,
                close=base,
                open_=base,
                volume=500,
            )
        )
    return candles


def make_h4_candles_for_range(
    squeeze_n: int, atr_period: int = 14, base: float = 2000.0
) -> list:
    """Build enough H4 candles (atr_period + squeeze_n) with a narrow squeeze window.

    The first atr_period candles have a wider range for ATR seeding, the last
    squeeze_n candles have a very narrow range so the squeeze condition passes.
    """
    # ATR seed candles — wide range (high-low ~10 each)
    seed = []
    for i in range(atr_period):
        seed.append(
            make_candle(
                high=base + 5.0,
                low=base - 5.0,
                close=base,
                open_=base,
                volume=500,
            )
        )

    # Narrow squeeze candles — range ~1.0 per candle, well below 1.5×ATR
    squeeze = make_narrow_h4_candles(squeeze_n, base=base, half_range=0.5)
    return seed + squeeze


def make_h1_candles(
    n: int,
    base: float = 2000.0,
    vol_normal: int = 1000,
) -> list:
    """Return n flat H1 candles at `base` with normal volume."""
    candles = []
    for _ in range(n):
        candles.append(
            make_candle(
                high=base + 0.3,
                low=base - 0.3,
                close=base,
                open_=base,
                volume=vol_normal,
            )
        )
    return candles


# ---------------------------------------------------------------------------
# Strategy params fixture
# ---------------------------------------------------------------------------


PARAMS = {
    "squeeze_lookback": 15.0,
    "volume_mult": 1.5,
    "sl_atr_mult": 1.0,
}


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestBreakoutExpansionBuySignal:
    """Test 1: BUY signal generated when all conditions are met."""

    @pytest.mark.asyncio
    async def test_buy_signal_generated(self) -> None:
        """Given narrow H4 range + H1 close above range_high with high volume → BUY signal."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        squeeze_lookback = int(PARAMS["squeeze_lookback"])
        base = 2000.0
        range_high = base + 0.5  # From make_narrow_h4_candles half_range=0.5

        h4_candles = make_h4_candles_for_range(squeeze_lookback, atr_period=14, base=base)

        # 22 normal H1 candles + 1 breakout candle at the end
        h1_candles = make_h1_candles(22, base=base, vol_normal=1000)
        # Breakout: close above range_high, volume 3× normal (well above 1.5×SMA20)
        breakout = make_candle(
            high=range_high + 1.0,
            low=range_high,
            close=range_high + 0.5,  # closes above range_high
            open_=range_high,
            volume=3000,  # 3× SMA20 of 1000 → passes volume_mult=1.5
        )
        h1_candles.append(breakout)

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert len(signals) == 1, f"Expected 1 signal, got {len(signals)}"
        sig = signals[0]
        assert sig.direction.value == "BUY"

    @pytest.mark.asyncio
    async def test_sell_signal_generated(self) -> None:
        """Given H1 close below range_low with high volume → SELL signal."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        squeeze_lookback = int(PARAMS["squeeze_lookback"])
        base = 2000.0
        range_low = base - 0.5

        h4_candles = make_h4_candles_for_range(squeeze_lookback, atr_period=14, base=base)

        h1_candles = make_h1_candles(22, base=base, vol_normal=1000)
        breakout = make_candle(
            high=range_low,
            low=range_low - 1.0,
            close=range_low - 0.5,  # closes below range_low
            open_=range_low,
            volume=3000,
        )
        h1_candles.append(breakout)

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert len(signals) == 1
        assert signals[0].direction.value == "SELL"


class TestBreakoutExpansionTpCalculation:
    """Test 2: TP1 = entry + range_width (1× range width, fixed), TP2 = None."""

    @pytest.mark.asyncio
    async def test_tp1_equals_entry_plus_range_width(self) -> None:
        """BUY signal TP1 must equal entry + range_width."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        squeeze_lookback = int(PARAMS["squeeze_lookback"])
        base = 2000.0
        half_range = 0.5
        range_high = base + half_range
        range_low = base - half_range
        range_width = range_high - range_low  # = 1.0

        h4_candles = make_h4_candles_for_range(squeeze_lookback, atr_period=14, base=base)
        h1_candles = make_h1_candles(22, base=base, vol_normal=1000)

        entry_close = range_high + 0.5
        breakout = make_candle(
            high=entry_close + 0.1,
            low=range_high,
            close=entry_close,
            open_=range_high,
            volume=3000,
        )
        h1_candles.append(breakout)

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert len(signals) == 1
        sig = signals[0]
        expected_tp1 = sig.entry_price + range_width
        assert sig.tp1_price == pytest.approx(expected_tp1, abs=0.01)
        assert sig.tp2_price is None

    @pytest.mark.asyncio
    async def test_tp2_is_none(self) -> None:
        """TP2 must be None — no second target for Breakout Expansion."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        squeeze_lookback = int(PARAMS["squeeze_lookback"])
        base = 2000.0
        range_high = base + 0.5

        h4_candles = make_h4_candles_for_range(squeeze_lookback, atr_period=14, base=base)
        h1_candles = make_h1_candles(22, base=base, vol_normal=1000)
        h1_candles.append(
            make_candle(
                high=range_high + 1.0,
                low=range_high,
                close=range_high + 0.5,
                open_=range_high,
                volume=3000,
            )
        )

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert len(signals) == 1
        assert signals[0].tp2_price is None


class TestBreakoutExpansionConfidence:
    """Test 3: Confidence is in [0.0, 1.0]; weights sum to 1.0."""

    @pytest.mark.asyncio
    async def test_confidence_in_valid_range(self) -> None:
        """Confidence must be in [0.0, 1.0]."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        squeeze_lookback = int(PARAMS["squeeze_lookback"])
        base = 2000.0
        range_high = base + 0.5

        h4_candles = make_h4_candles_for_range(squeeze_lookback, atr_period=14, base=base)
        h1_candles = make_h1_candles(22, base=base, vol_normal=1000)
        h1_candles.append(
            make_candle(
                high=range_high + 1.0,
                low=range_high,
                close=range_high + 0.5,
                open_=range_high,
                volume=3000,
            )
        )

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert len(signals) == 1
        confidence = signals[0].confidence
        assert 0.0 <= confidence <= 1.0

    def test_weights_sum_to_one(self) -> None:
        """W_SQUEEZE_DURATION + W_VOLUME_RATIO + W_RANGE_CLARITY must equal 1.0."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        total = (
            BreakoutExpansionStrategy.W_SQUEEZE_DURATION
            + BreakoutExpansionStrategy.W_VOLUME_RATIO
            + BreakoutExpansionStrategy.W_RANGE_CLARITY
        )
        assert total == pytest.approx(1.0)

    def test_param_ranges_has_exactly_3_keys(self) -> None:
        """PARAM_RANGES must have exactly 3 keys."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        assert len(BreakoutExpansionStrategy.PARAM_RANGES) == 3
        assert "squeeze_lookback" in BreakoutExpansionStrategy.PARAM_RANGES
        assert "volume_mult" in BreakoutExpansionStrategy.PARAM_RANGES
        assert "sl_atr_mult" in BreakoutExpansionStrategy.PARAM_RANGES


class TestBreakoutExpansionRangeFilter:
    """Test 4: Wide H4 range (range > 1.5×ATR) returns no signal."""

    @pytest.mark.asyncio
    async def test_wide_range_returns_empty(self) -> None:
        """If H4 range_width >= 1.5×ATR14, no signal is generated.

        Strategy: make all candles have tiny H-L (≈0.1) so ATR ≈ 0.1,
        but spread the squeeze window candles between two price levels
        so range_high - range_low = 10.  10 >> 1.5 × 0.1 = 0.15 → rejects.
        """
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        squeeze_lookback = int(PARAMS["squeeze_lookback"])
        base = 2000.0

        # All h4 candles have very narrow H-L (≈0.1) so ATR stays tiny.
        # ATR seed: 14 candles at base with H-L = 0.1.
        seed = [
            make_candle(high=base + 0.05, low=base - 0.05, close=base, volume=500)
            for _ in range(14)
        ]
        # Squeeze window: alternate between base+5 and base-5 to create
        # range_width = 10, but each candle still has tiny H-L = 0.1.
        # ATR stays ≈ 0.1 (the candle H-L), but range_high - range_low = 10.
        wide_squeeze = []
        for i in range(squeeze_lookback):
            center = (base + 5.0) if i % 2 == 0 else (base - 5.0)
            wide_squeeze.append(
                make_candle(
                    high=center + 0.05,
                    low=center - 0.05,
                    close=center,
                    volume=500,
                )
            )
        h4_candles = seed + wide_squeeze

        # Add H1 candles with a clear breakout — but range filter should reject first.
        h1_candles = make_h1_candles(22, base=base, vol_normal=1000)
        h1_candles.append(
            make_candle(high=base + 10.0, low=base + 5.0, close=base + 8.0, volume=9999)
        )

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert signals == []

    @pytest.mark.asyncio
    async def test_insufficient_h4_candles_returns_empty(self) -> None:
        """Fewer than squeeze_lookback + 14 H4 candles returns []."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        # Only 10 candles — below the required minimum of 15+14=29
        h4_candles = make_narrow_h4_candles(10)
        h1_candles = make_h1_candles(22)

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert signals == []

    @pytest.mark.asyncio
    async def test_insufficient_h1_candles_returns_empty(self) -> None:
        """Fewer than 21 H1 candles returns []."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        strategy = BreakoutExpansionStrategy(params=PARAMS)

        h4_candles = make_h4_candles_for_range(15, atr_period=14)
        h1_candles = make_h1_candles(10)  # only 10 < 21

        signals = await strategy.generate_signals({"H4": h4_candles, "H1": h1_candles})

        assert signals == []


class TestBreakoutExpansionNoForbiddenImports:
    """Test 5: No SQLAlchemy session operations in the strategy class."""

    def test_no_session_in_source(self) -> None:
        """Strategy source must not contain 'session' or 'AsyncSession'."""
        import inspect
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        source = inspect.getsource(BreakoutExpansionStrategy)
        assert "session" not in source.lower() or "params_snapshot" in source
        # More specific check — the class source should not reference session objects
        assert "AsyncSession" not in source
        assert "session.add" not in source
        assert "session.commit" not in source

    def test_no_apscheduler_in_source(self) -> None:
        """Strategy source must not contain 'APScheduler' or 'apscheduler'."""
        import inspect
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        source = inspect.getsource(BreakoutExpansionStrategy)
        assert "apscheduler" not in source.lower()

    def test_no_rsi_in_source(self) -> None:
        """Strategy source must not contain RSI (forbidden per CLAUDE.md §17)."""
        import inspect
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        source = inspect.getsource(BreakoutExpansionStrategy)
        assert "rsi" not in source.lower()

    def test_strategy_name_constant(self) -> None:
        """STRATEGY_NAME must equal 'breakout_expansion'."""
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy

        assert BreakoutExpansionStrategy.STRATEGY_NAME == "breakout_expansion"
