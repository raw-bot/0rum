"""Frozen candle fixtures for Phase 3 strategy tests.

All fixtures use MagicMock objects — no real Candle ORM, no DB dependency.
500 candles per timeframe, deterministic sine-wave price pattern.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def make_candles(
    timeframe: str,
    count: int,
    base_close: float = 2300.0,
    trend_after: int = 300,
) -> list:
    """Build a list of MagicMock candle objects with deterministic OHLCV data.

    Price pattern: flat sine wave for first `trend_after` candles, then
    adds a linear upward drift so EMA(50) > EMA(200) near the end.

    Args:
        timeframe: One of "M15", "H1", "H4", "D1".
        count: Number of candles to generate.
        base_close: Starting close price level.
        trend_after: Index after which a linear uptrend is added.

    Returns:
        List of MagicMock candle objects with all required ORM attributes set.
    """
    candles: list = []
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    intervals: dict[str, timedelta] = {
        "M15": timedelta(minutes=15),
        "H1": timedelta(hours=1),
        "H4": timedelta(hours=4),
        "D1": timedelta(days=1),
    }
    step = intervals[timeframe]

    for i in range(count):
        trend = 0.0 if i < trend_after else (i - trend_after) * 0.05
        close = base_close + trend + 15 * math.sin(i * 0.3)
        high = close + abs(5 * math.sin(i * 0.7 + 1))
        low = close - abs(5 * math.sin(i * 0.5 + 2))
        open_ = close - 2 * math.sin(i * 0.2)
        volume = 1000 + int(500 * abs(math.sin(i * 0.4)))

        c = MagicMock()
        c.instrument = "XAUUSD"
        c.timeframe = timeframe
        c.timestamp = ts
        c.open = Decimal(str(round(open_, 5)))
        c.high = Decimal(str(round(high, 5)))
        c.low = Decimal(str(round(low, 5)))
        c.close = Decimal(str(round(close, 5)))
        c.volume = volume
        c.complete = True
        candles.append(c)
        ts += step

    return candles


# ---------------------------------------------------------------------------
# Candle fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def candles_m15() -> list:
    """500 M15 candles — sine wave around 2300, linear uptrend after index 300."""
    return make_candles("M15", 500)


@pytest.fixture
def candles_h1() -> list:
    """500 H1 candles — uptrend after index 300 ensures EMA50 > EMA200 at the end."""
    return make_candles("H1", 500)


@pytest.fixture
def candles_h4() -> list:
    """500 H4 candles — last 25 are a tight consolidation; last candle has a volume spike."""
    candles = make_candles("H4", 500)
    # Tight range: forces squeeze_lookback condition for BreakoutExpansion tests
    for c in candles[-25:]:
        c.close = Decimal("2320.0")
        c.high = Decimal("2322.0")
        c.low = Decimal("2318.0")
        c.open = Decimal("2319.5")
    # Volume spike on last candle — simulates imminent breakout
    candles[-1].volume = 5000
    return candles


@pytest.fixture
def candles_d1() -> list:
    """200 D1 candles."""
    return make_candles("D1", 200)


@pytest.fixture
def candles_dict(candles_m15, candles_h1, candles_h4, candles_d1) -> dict:
    """All four timeframes bundled into the dict format strategies expect."""
    return {
        "M15": candles_m15,
        "H1": candles_h1,
        "H4": candles_h4,
        "D1": candles_d1,
    }


# ---------------------------------------------------------------------------
# Strategy parameter fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def liquidity_sweep_params() -> dict:
    return {"sweep_atr_mult": 0.5, "sl_atr_mult": 0.65, "tp_risk_mult": 2.0}


@pytest.fixture
def trend_cont_params() -> dict:
    return {"pullback_ema": 20.0, "sl_atr_mult": 0.5, "tp_risk_mult": 2.0}


@pytest.fixture
def breakout_params() -> dict:
    return {"squeeze_lookback": 20.0, "volume_mult": 1.5, "sl_atr_mult": 1.0}


@pytest.fixture
def ema_momentum_params() -> dict:
    return {"fast_ema": 8.0, "slow_ema": 21.0, "sl_atr_mult": 0.5}
