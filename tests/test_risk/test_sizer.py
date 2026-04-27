"""Tests for src/risk/sizer.py — pure-function ATR-based position sizer (RISK-04).

Per 06-CONTEXT D-07: order vol -> cap -> concentration.
Per D-16: symmetric low-vol +30% branch in scope.
Per Pitfall 1: atr_pctile is 0.0-1.0 scale, settings are 0-100 — sizer normalizes settings.
"""

from decimal import Decimal

import pytest

from src.risk.sizer import calculate_position_size

# Shared kwargs — each test overrides only what it cares about.
COMMON = dict(
    equity=Decimal("10000"),
    entry_price=Decimal("2340.00"),
    sl_price=Decimal("2325.00"),
    atr_value=Decimal("15.0"),
    atr_high_vol_pctile=90,
    atr_low_vol_pctile=10,
    hard_cap=0.02,
)


def test_baseline_normal_vol_no_concentration():
    """Normal volatility regime, no concentration: vol_factor == 1.0, risk unchanged."""
    s = calculate_position_size(
        **COMMON,
        risk_per_trade=0.01,
        atr_pctile=0.5,
        same_direction_open_count=0,
    )
    assert s.vol_factor == 1.0
    assert s.risk_pct == pytest.approx(0.01)
    assert s.concentration_reduced is False
    assert s.risk_amount_usd == Decimal("100")
    assert s.size_lots > 0


def test_high_vol_reduces_30_percent():
    """atr_pctile=0.95 >= high_threshold=0.90 -> vol_factor=0.7, risk_pct reduced."""
    s = calculate_position_size(
        **COMMON,
        risk_per_trade=0.01,
        atr_pctile=0.95,
        same_direction_open_count=0,
    )
    assert s.vol_factor == 0.7
    assert s.risk_pct == pytest.approx(0.007)


def test_low_vol_increases_30_percent():
    """atr_pctile=0.05 <= low_threshold=0.10 -> vol_factor=1.3, risk_pct increased.

    LOCKS D-16: symmetric low-vol +30% branch is in scope.
    0.01 * 1.3 = 0.013, which is below hard_cap=0.02 so no clamping.
    """
    s = calculate_position_size(
        **COMMON,
        risk_per_trade=0.01,
        atr_pctile=0.05,
        same_direction_open_count=0,
    )
    assert s.vol_factor == 1.3
    assert s.risk_pct == pytest.approx(0.013)


def test_hard_cap_clamps_after_low_vol_bump():
    """Vol bump then cap: 0.018 * 1.3 = 0.0234, capped at 0.02.

    LOCKS D-07 vol-then-cap order. Cap-then-vol would yield risk_pct > 0.02.
    """
    s = calculate_position_size(
        **COMMON,
        risk_per_trade=0.018,
        atr_pctile=0.05,
        same_direction_open_count=0,
    )
    assert s.vol_factor == 1.3
    assert s.risk_pct == pytest.approx(0.02)


def test_concentration_halves_after_cap():
    """Full chain: 0.018 * 1.3 = 0.0234 -> cap -> 0.02 -> * 0.5 -> 0.01.

    LOCKS D-04 + D-07 order: concentration halving is AFTER the hard cap.
    """
    s = calculate_position_size(
        **COMMON,
        risk_per_trade=0.018,
        atr_pctile=0.05,
        same_direction_open_count=4,
    )
    assert s.risk_pct == pytest.approx(0.01)
    assert s.concentration_reduced is True


def test_atr_pctile_scale_invariant():
    """atr_pctile=0.95 (0.0-1.0 scale, NOT 95) triggers high-vol branch.

    LOCKS Pitfall 1: if the sizer compared raw atr_pctile against 90 instead of
    normalizing settings (90 -> 0.90), this test would return vol_factor=1.0.
    """
    s = calculate_position_size(
        **COMMON,
        risk_per_trade=0.01,
        atr_pctile=0.95,
        same_direction_open_count=0,
    )
    assert s.vol_factor == 0.7


def test_size_lots_zero_when_no_sl_distance():
    """entry_price == sl_price -> sl_distance == 0 -> size_lots == Decimal('0'), no exception."""
    s = calculate_position_size(
        equity=Decimal("10000"),
        entry_price=Decimal("2340.00"),
        sl_price=Decimal("2340.00"),
        atr_value=Decimal("15.0"),
        atr_high_vol_pctile=90,
        atr_low_vol_pctile=10,
        hard_cap=0.02,
        risk_per_trade=0.01,
        atr_pctile=0.5,
        same_direction_open_count=0,
    )
    assert s.size_lots == Decimal("0")
    assert s.risk_pct == pytest.approx(0.01)


def test_size_lots_quantized_to_two_decimals():
    """size_lots must be quantized to 0.01 (XAUUSD: 1 lot = 100 oz).

    Uses same inputs as test_baseline_normal_vol_no_concentration.
    """
    s = calculate_position_size(
        **COMMON,
        risk_per_trade=0.01,
        atr_pctile=0.5,
        same_direction_open_count=0,
    )
    # Decimal.as_tuple().exponent is -2 for quantize(Decimal("0.01"))
    assert s.size_lots.as_tuple().exponent == -2
