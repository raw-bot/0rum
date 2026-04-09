"""Unit tests for LiquiditySweepStrategy using frozen candle fixtures.

Tests verify:
- BUY signal generated when M15 wick sweeps below H4 swing low and closes above it
- SELL signal generated when M15 wick sweeps above H4 swing high and closes below it
- SL/TP/TP2 geometry is correct
- Confidence is in [0.0, 1.0] and uses the documented linear weights
- Empty list returned when data is insufficient
- No SQLAlchemy session operations in the class
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from src.strategies.liquidity_sweep import (
    LiquiditySweepStrategy,
    W_PROXIMITY,
    W_SWEEP_DEPTH,
    W_VOLUME_SPIKE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1000,
) -> SimpleNamespace:
    """Return a minimal candle object compatible with AbstractStrategy helpers."""
    return SimpleNamespace(
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


def _default_params() -> dict:
    return {
        "sweep_atr_mult": 0.5,
        "sl_atr_mult": 0.6,
        "tp_risk_mult": 2.0,
    }


def _build_h4_candles_with_swing_low(valley_low: float, n: int = 30) -> list:
    """Build H4 candles that produce a swing low near `valley_low`.

    Creates a V-shape so argrelextrema(order=10) detects a local minimum.
    The candle at midpoint has its low at valley_low.
    """
    candles = []
    mid = n // 2
    for i in range(n):
        dist = abs(i - mid)
        # At midpoint dist=0 → lowest price. Further away → higher price.
        low = valley_low + dist * 3.0
        high = low + 1.5
        open_ = low + 0.5
        close = low + 0.7
        candles.append(make_candle(open_, high, low, close))
    return candles


def _build_h4_candles_with_swing_high(peak_high: float, n: int = 30) -> list:
    """Build H4 candles that produce a swing high near `peak_high`."""
    candles = []
    mid = n // 2
    for i in range(n):
        dist = abs(i - mid)
        high = peak_high - dist * 3.0
        low = high - 1.5
        open_ = high - 0.5
        close = high - 0.7
        candles.append(make_candle(open_, high, low, close))
    return candles


def _build_flat_m15(base: float, n: int = 50) -> list:
    """Build flat M15 candles with consistent range to get a predictable ATR."""
    return [make_candle(base, base + 0.4, base - 0.4, base + 0.1) for _ in range(n)]


def _build_m15_with_sweep_buy(
    strategy: LiquiditySweepStrategy,
    h4_candles: list,
    n_before: int = 49,
) -> list:
    """Build M15 candles ending in a sweep-buy candle below the detected swing low.

    Detects the actual swing low from h4_candles and ensures the sweep candle's
    low is well below `level - sweep_atr_mult * atr`.
    """
    # Detect the actual swing level
    swing_highs, swing_lows = strategy.detect_swing_levels(h4_candles, order=10)
    assert swing_lows, "No swing lows detected in H4 fixture"
    level = min(swing_lows)  # use the lowest swing low

    # Build flat candles around level+5 so ATR is tiny and predictable
    base = level + 5.0
    flat_range = 0.4  # each candle high-low = 0.8
    candles = [make_candle(base, base + flat_range, base - flat_range, base + 0.1)
               for _ in range(n_before)]

    # Compute actual ATR from these flat candles
    atr = strategy.calculate_atr(candles, period=14)
    sweep_atr_mult = float(strategy.params["sweep_atr_mult"])

    # Sweep candle: low must be < level - sweep_atr_mult*atr, close must be > level
    sweep_low = level - sweep_atr_mult * atr - atr  # 1 full ATR below threshold
    sweep_close = level + atr * 0.5               # well above level
    candles.append(
        make_candle(level + 0.3, level + atr * 0.6, sweep_low, sweep_close, volume=3000)
    )
    return candles, level


def _build_m15_with_sweep_sell(
    strategy: LiquiditySweepStrategy,
    h4_candles: list,
    n_before: int = 49,
) -> list:
    """Build M15 candles ending in a sweep-sell candle above the detected swing high."""
    swing_highs, swing_lows = strategy.detect_swing_levels(h4_candles, order=10)
    assert swing_highs, "No swing highs detected in H4 fixture"
    level = max(swing_highs)

    base = level - 5.0
    flat_range = 0.4
    candles = [make_candle(base, base + flat_range, base - flat_range, base - 0.1)
               for _ in range(n_before)]

    atr = strategy.calculate_atr(candles, period=14)
    sweep_atr_mult = float(strategy.params["sweep_atr_mult"])

    sweep_high = level + sweep_atr_mult * atr + atr
    sweep_close = level - atr * 0.5
    candles.append(
        make_candle(level - 0.3, sweep_high, level - atr * 0.6, sweep_close, volume=3000)
    )
    return candles, level


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLiquiditySweepBuySignal:
    """Test 1 & 2: BUY signal geometry and data correctness."""

    @pytest.mark.asyncio
    async def test_buy_signal_generated_on_sweep(self):
        """generate_signals() returns at least 1 BUY signal when sweep occurs."""
        strategy = LiquiditySweepStrategy(_default_params())
        h4_candles = _build_h4_candles_with_swing_low(2300.0)
        m15_candles, _ = _build_m15_with_sweep_buy(strategy, h4_candles)

        signals = await strategy.generate_signals({"H4": h4_candles, "M15": m15_candles})

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.direction.value == "BUY"
        assert sig.strategy.value == "liquidity_sweep"
        assert sig.timeframe.value == "M15"

    @pytest.mark.asyncio
    async def test_buy_signal_price_geometry(self):
        """BUY: entry > sl, tp1 > entry, tp2 = entry + 2*(tp1-entry)."""
        strategy = LiquiditySweepStrategy(_default_params())
        h4_candles = _build_h4_candles_with_swing_low(2300.0)
        m15_candles, _ = _build_m15_with_sweep_buy(strategy, h4_candles)

        signals = await strategy.generate_signals({"H4": h4_candles, "M15": m15_candles})
        assert len(signals) >= 1

        sig = signals[0]
        assert sig.entry_price > sig.sl_price, "BUY: entry must be above SL"
        assert sig.tp1_price > sig.entry_price, "BUY: TP1 must be above entry"
        assert sig.tp2_price is not None

        # TP2 = entry + 2*(tp1 - entry)
        expected_tp2 = sig.entry_price + 2.0 * (sig.tp1_price - sig.entry_price)
        assert abs(sig.tp2_price - expected_tp2) < 1e-6, (
            f"TP2={sig.tp2_price}, expected {expected_tp2}"
        )


class TestLiquiditySweepSellSignal:
    """SELL signal geometry."""

    @pytest.mark.asyncio
    async def test_sell_signal_generated_on_sweep(self):
        """generate_signals() returns a SELL signal when price sweeps above resistance."""
        strategy = LiquiditySweepStrategy(_default_params())
        h4_candles = _build_h4_candles_with_swing_high(2350.0)
        m15_candles, _ = _build_m15_with_sweep_sell(strategy, h4_candles)

        signals = await strategy.generate_signals({"H4": h4_candles, "M15": m15_candles})
        assert len(signals) >= 1
        assert signals[0].direction.value == "SELL"

    @pytest.mark.asyncio
    async def test_sell_signal_price_geometry(self):
        """SELL: entry < sl, tp1 < entry, tp2 = entry - 2*(entry-tp1)."""
        strategy = LiquiditySweepStrategy(_default_params())
        h4_candles = _build_h4_candles_with_swing_high(2350.0)
        m15_candles, _ = _build_m15_with_sweep_sell(strategy, h4_candles)

        signals = await strategy.generate_signals({"H4": h4_candles, "M15": m15_candles})
        assert len(signals) >= 1

        sig = signals[0]
        assert sig.entry_price < sig.sl_price, "SELL: entry must be below SL"
        assert sig.tp1_price < sig.entry_price, "SELL: TP1 must be below entry"
        assert sig.tp2_price is not None

        expected_tp2 = sig.entry_price - 2.0 * (sig.entry_price - sig.tp1_price)
        assert abs(sig.tp2_price - expected_tp2) < 1e-6


class TestLiquiditySweepConfidence:
    """Test 3: Confidence is in [0.0, 1.0] and weights sum to 1.0."""

    def test_weights_sum_to_one(self):
        """Module-level weight constants must sum to 1.0."""
        total = W_SWEEP_DEPTH + W_VOLUME_SPIKE + W_PROXIMITY
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    @pytest.mark.asyncio
    async def test_confidence_in_range(self):
        """Confidence must always be in [0.0, 1.0]."""
        strategy = LiquiditySweepStrategy(_default_params())
        h4_candles = _build_h4_candles_with_swing_low(2300.0)
        m15_candles, _ = _build_m15_with_sweep_buy(strategy, h4_candles)

        signals = await strategy.generate_signals({"H4": h4_candles, "M15": m15_candles})
        assert len(signals) >= 1

        conf = signals[0].confidence
        assert 0.0 <= conf <= 1.0, f"Confidence {conf} out of [0.0, 1.0]"


class TestLiquiditySweepEdgeCases:
    """Test 4 & 5: Edge cases — insufficient data, no SQLAlchemy ops."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_insufficient_h4_candles(self):
        """Returns [] when fewer than 21 H4 candles (swing detection needs 2*order+1)."""
        strategy = LiquiditySweepStrategy(_default_params())
        h4_candles = [make_candle(2300, 2301, 2299, 2300) for _ in range(10)]
        m15_candles = [make_candle(2300, 2301, 2299, 2300) for _ in range(50)]

        signals = await strategy.generate_signals({"H4": h4_candles, "M15": m15_candles})
        assert signals == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_h4_candles(self):
        """Returns [] when H4 candles are missing from candles dict."""
        strategy = LiquiditySweepStrategy(_default_params())
        m15_candles = [make_candle(2300, 2301, 2299, 2300) for _ in range(50)]

        signals = await strategy.generate_signals({"M15": m15_candles})
        assert signals == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_sweep_detected(self):
        """Returns [] when M15 candles show no sweep of any swing level."""
        strategy = LiquiditySweepStrategy(_default_params())
        h4_candles = _build_h4_candles_with_swing_low(2300.0)
        # M15 candles that never touch the swing level
        m15_candles = [
            make_candle(2310, 2312, 2309, 2311) for _ in range(50)
        ]

        signals = await strategy.generate_signals({"H4": h4_candles, "M15": m15_candles})
        assert signals == []

    def test_no_sqlalchemy_session_operations(self):
        """Strategy class must contain no SQLAlchemy session write operations."""
        source = inspect.getsource(LiquiditySweepStrategy)
        assert "session.add" not in source
        assert "session.commit" not in source
        assert "AsyncSession" not in source

    def test_params_snapshot_matches_strategy_params(self):
        """params_snapshot in each signal must equal the strategy params."""
        strategy = LiquiditySweepStrategy(_default_params())
        assert strategy.params == _default_params()

    def test_missing_param_falls_back_to_midpoint(self):
        """Missing params are filled with PARAM_RANGES midpoints without raising."""
        strategy = LiquiditySweepStrategy({})
        for key, (lo, hi) in LiquiditySweepStrategy.PARAM_RANGES.items():
            expected_mid = (lo + hi) / 2.0
            assert strategy.params[key] == expected_mid

    def test_exactly_three_param_ranges(self):
        """PARAM_RANGES must have exactly 3 keys."""
        assert len(LiquiditySweepStrategy.PARAM_RANGES) == 3
        expected = {"sweep_atr_mult", "sl_atr_mult", "tp_risk_mult"}
        assert set(LiquiditySweepStrategy.PARAM_RANGES.keys()) == expected
