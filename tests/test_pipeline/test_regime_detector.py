"""Unit tests for src/backtesting/regime_detector.py — market regime classification."""

import unittest.mock as mock
from types import SimpleNamespace

import pytest

from src.backtesting.regime_detector import RegimeDetector
from src.models.signal_data import MarketRegimeType


def make_candle(close=2340.0, high=2345.0, low=2335.0):
    """Create a minimal candle-like object using SimpleNamespace (no DB needed)."""
    return SimpleNamespace(close=close, high=high, low=low)


@pytest.mark.asyncio
async def test_regime_high_vol_overrides_all():
    """ATR percentile >= 0.90 → HIGH_VOL (overrides trending/ranging classification)."""
    candles = [make_candle(close=2000 + i, high=2100 + i, low=1900 + i) for i in range(300)]
    detector = RegimeDetector()
    with mock.patch.object(detector, "_calculate_atr_percentile", return_value=0.95):
        regime = await detector.detect(candles)
    assert regime.regime == MarketRegimeType.HIGH_VOL


@pytest.mark.asyncio
async def test_regime_trending_up():
    """ADX > 25, EMA50 > EMA200, pctile=0.5 → TRENDING_UP."""
    candles = [make_candle(close=2000 + i * 0.5) for i in range(250)]
    detector = RegimeDetector()
    with (
        mock.patch.object(detector, "_calculate_atr_percentile", return_value=0.50),
        mock.patch.object(detector, "_calculate_adx", return_value=30.0),
        mock.patch.object(detector, "_calculate_ema", side_effect=[2200.0, 2100.0]),  # ema50 > ema200
    ):
        regime = await detector.detect(candles)
    assert regime.regime == MarketRegimeType.TRENDING_UP


@pytest.mark.asyncio
async def test_regime_trending_down():
    """ADX > 25, EMA50 < EMA200, pctile=0.5 → TRENDING_DOWN."""
    candles = [make_candle(close=2000 - i * 0.5) for i in range(250)]
    detector = RegimeDetector()
    with (
        mock.patch.object(detector, "_calculate_atr_percentile", return_value=0.50),
        mock.patch.object(detector, "_calculate_adx", return_value=30.0),
        mock.patch.object(detector, "_calculate_ema", side_effect=[2100.0, 2200.0]),  # ema50 < ema200
    ):
        regime = await detector.detect(candles)
    assert regime.regime == MarketRegimeType.TRENDING_DOWN


@pytest.mark.asyncio
async def test_regime_ranging_default():
    """ADX <= 25, pctile=0.4 → RANGING (default fallback)."""
    candles = [make_candle() for _ in range(50)]
    detector = RegimeDetector()
    with (
        mock.patch.object(detector, "_calculate_atr_percentile", return_value=0.40),
        mock.patch.object(detector, "_calculate_adx", return_value=18.0),
        mock.patch.object(detector, "_calculate_ema", side_effect=[2340.0, 2340.0]),
    ):
        regime = await detector.detect(candles)
    assert regime.regime == MarketRegimeType.RANGING


def test_regime_atr_returns_zero_insufficient_data():
    """Fewer than period+1 candles → _calculate_atr returns 0.0."""
    detector = RegimeDetector()
    result = detector._calculate_atr([make_candle() for _ in range(5)], period=14)
    assert result == 0.0
