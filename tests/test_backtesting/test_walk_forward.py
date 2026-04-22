"""Unit tests for src/backtesting/walk_forward.py."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.backtesting.walk_forward import (
    MIN_CANDLES_FOR_OPTIMIZER,
    TradeOutcome,
    WalkForwardWindow,
    _check_sufficient_data,
    _compute_profit_factor,
    _compute_wfe,
    build_windows,
    multi_window_gate_passes,
    simulate_trade_outcome,
)
from tests.test_backtesting.conftest import make_candle

NOW = datetime(2026, 4, 23, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# build_windows
# ---------------------------------------------------------------------------


def test_build_windows_returns_three_windows():
    windows = build_windows(NOW, train_months=6, test_months=2, n_windows=3)
    assert len(windows) == 3


def test_window_num_ascending():
    windows = build_windows(NOW, train_months=6, test_months=2, n_windows=3)
    assert [w.window_num for w in windows] == [1, 2, 3]


def test_window_oos_periods_nonoverlapping():
    windows = build_windows(NOW, train_months=6, test_months=2, n_windows=3)
    for i in range(len(windows) - 1):
        assert windows[i].oos_end == windows[i + 1].oos_start


def test_window_train_end_equals_oos_start():
    windows = build_windows(NOW, train_months=6, test_months=2, n_windows=3)
    for w in windows:
        assert w.train_end == w.oos_start


def test_most_recent_window_oos_ends_at_anchor():
    windows = build_windows(NOW, train_months=6, test_months=2, n_windows=3)
    assert windows[-1].oos_end == NOW


def test_train_window_duration():
    windows = build_windows(NOW, train_months=6, test_months=2, n_windows=3)
    expected_train_days = timedelta(days=6 * 21)
    for w in windows:
        assert w.train_end - w.train_start == expected_train_days


# ---------------------------------------------------------------------------
# _compute_profit_factor
# ---------------------------------------------------------------------------


def test_compute_profit_factor_normal_case():
    # winners: 10 + 20 = 30; losers: 5 + 3 = 8; pf = 30/8 = 3.75
    pnl = np.array([10.0, 20.0, -5.0, -3.0])
    assert abs(_compute_profit_factor(pnl) - 3.75) < 1e-9


def test_compute_profit_factor_no_losers_returns_zero():
    pnl = np.array([10.0, 20.0])
    assert _compute_profit_factor(pnl) == 0.0


def test_compute_profit_factor_all_losers():
    pnl = np.array([-5.0, -3.0])
    assert _compute_profit_factor(pnl) == 0.0


# ---------------------------------------------------------------------------
# _compute_wfe
# ---------------------------------------------------------------------------


def test_compute_wfe_normal():
    assert abs(_compute_wfe(is_pf=2.0, oos_pf=1.0) - 0.5) < 1e-9


def test_compute_wfe_zero_is_pf_guard():
    assert _compute_wfe(is_pf=0.0, oos_pf=1.0) == 0.0


def test_wfe_gate_blocks_low_wfe():
    wfe = _compute_wfe(is_pf=3.0, oos_pf=1.0)
    assert wfe < 0.50


# ---------------------------------------------------------------------------
# multi_window_gate_passes
# ---------------------------------------------------------------------------


def test_multiwindow_2_of_3_passes():
    assert multi_window_gate_passes([1.5, 1.2, 0.8]) is True


def test_multiwindow_1_of_3_fails():
    assert multi_window_gate_passes([1.5, 0.9, 0.8]) is False


def test_multiwindow_0_of_3_fails():
    assert multi_window_gate_passes([0.5, 0.6, 0.7]) is False


def test_multiwindow_3_of_3_passes():
    assert multi_window_gate_passes([1.1, 1.2, 1.3]) is True


# ---------------------------------------------------------------------------
# _check_sufficient_data
# ---------------------------------------------------------------------------


def test_data_guard_passes_at_minimum():
    candles = {
        "D1": [None] * 257,
        "H4": [None] * 1542,
        "H1": [None] * 6171,
        "M15": [None] * 24685,
    }
    assert _check_sufficient_data(candles) is True


def test_data_guard_fails_below_minimum_d1():
    candles = {
        "D1": [None] * 256,  # one below minimum
        "H4": [None] * 1542,
        "H1": [None] * 6171,
        "M15": [None] * 24685,
    }
    assert _check_sufficient_data(candles) is False


def test_data_guard_fails_missing_timeframe():
    candles = {
        "D1": [None] * 257,
        "H4": [None] * 1542,
        # H1 missing
        "M15": [None] * 24685,
    }
    assert _check_sufficient_data(candles) is False


# ---------------------------------------------------------------------------
# simulate_trade_outcome — BUY direction
# ---------------------------------------------------------------------------


def _make_sl_hit_candle(entry, sl):
    """Candle whose low hits the SL (BUY scenario)."""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return make_candle(ts, close=entry, high=entry + 5, low=sl - 0.01)


def _make_tp1_hit_candle(entry, tp1):
    """Candle whose high hits TP1 (BUY scenario)."""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return make_candle(ts, close=entry, high=tp1 + 0.01, low=entry - 1)


def test_simulate_trade_outcome_buy_sl():
    candle = _make_sl_hit_candle(entry=2300, sl=2290)
    outcome, pnl = simulate_trade_outcome("BUY", 2300, 2290, 2315, None, [candle])
    assert outcome == TradeOutcome.SL
    assert abs(pnl - (-10.0)) < 1e-9


def test_simulate_trade_outcome_buy_tp1():
    candle = _make_tp1_hit_candle(entry=2300, tp1=2315)
    outcome, pnl = simulate_trade_outcome("BUY", 2300, 2290, 2315, None, [candle])
    assert outcome == TradeOutcome.TP1
    assert abs(pnl - 15.0) < 1e-9


def test_simulate_trade_outcome_sell_sl():
    # SL above entry for SELL
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(ts, close=2300, high=2310 + 0.01, low=2295)
    outcome, pnl = simulate_trade_outcome("SELL", 2300, 2310, 2285, None, [candle])
    assert outcome == TradeOutcome.SL
    assert abs(pnl - (-10.0)) < 1e-9


def test_simulate_trade_outcome_open_no_hit():
    # Candle that hits neither SL nor TP
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candle = make_candle(ts, close=2300, high=2305, low=2295)
    outcome, pnl = simulate_trade_outcome("BUY", 2300, 2290, 2315, None, [candle])
    assert outcome == TradeOutcome.OPEN
    assert pnl == 0.0
