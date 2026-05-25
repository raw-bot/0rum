"""Unit tests for src/backtesting/walk_forward.py."""

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.backtesting.execution_costs import adjust_entry, adjust_exit
from src.backtesting.walk_forward import (
    MIN_CANDLES_FOR_OPTIMIZER,
    TradeOutcome,
    WalkForwardWindow,
    _check_sufficient_data,
    _compute_profit_factor,
    _compute_wfe,
    build_windows,
    multi_window_gate_passes,
    simulate_signal_mode_trade_outcome,
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


def test_compute_profit_factor_all_winners_returns_inf():
    pnl = np.array([10.0, 20.0])
    assert math.isinf(_compute_profit_factor(pnl))


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
# execution costs
# ---------------------------------------------------------------------------


def test_adjust_entry_applies_half_spread_plus_slippage():
    assert adjust_entry(
        "BUY",
        Decimal("3300"),
        Decimal("0.30"),
        Decimal("0.10"),
    ) == Decimal("3300.25")
    assert adjust_entry(
        "SELL",
        Decimal("3300"),
        Decimal("0.30"),
        Decimal("0.10"),
    ) == Decimal("3299.75")


def test_adjust_exit_is_symmetric_to_entry():
    assert adjust_exit(
        "BUY",
        Decimal("3300"),
        Decimal("0.30"),
        Decimal("0.10"),
    ) == Decimal("3299.75")
    assert adjust_exit(
        "SELL",
        Decimal("3300"),
        Decimal("0.30"),
        Decimal("0.10"),
    ) == Decimal("3300.25")


def _make_h1_context(start, count=20, close=100.0, high=101.0, low=99.0):
    return [
        make_candle(start + timedelta(hours=i), close=close, high=high, low=low)
        for i in range(count)
    ]


def test_signal_mode_simulation_uses_tp1_partial_and_trailing_blended_pnl():
    """Backtest validation must mirror signal-mode TP1 partial + ATR trailing."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_context = _make_h1_context(start, count=20, close=100.0, high=101.0, low=99.0)
    m15_candles = [
        make_candle(start + timedelta(hours=20), close=112, high=113, low=108),
        make_candle(start + timedelta(hours=20, minutes=15), close=109, high=111, low=109),
    ]

    outcome, pnl_pct = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("100"),
        sl=Decimal("90"),
        tp1=Decimal("112"),
        tp2=Decimal("140"),
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("1"),
        equity_at_open=Decimal("100"),
        contract_size=Decimal("1"),
        spread_usd=Decimal("0"),
        slippage_usd=Decimal("0"),
    )

    assert outcome == TradeOutcome.TRAIL
    assert pnl_pct == pytest.approx(0.11)


def test_signal_mode_simulation_trail_wins_post_tp1_tie():
    """Post-TP1 tie must match live monitor: trailing stop wins before TP2."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_context = _make_h1_context(start, count=20, close=100.0, high=101.0, low=99.0)
    m15_candles = [
        make_candle(start + timedelta(hours=20), close=112, high=113, low=108),
        make_candle(start + timedelta(hours=20, minutes=15), close=118, high=130, low=111),
    ]

    outcome, pnl_pct = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("100"),
        sl=Decimal("90"),
        tp1=Decimal("112"),
        tp2=Decimal("125"),
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("1"),
        equity_at_open=Decimal("100"),
        contract_size=Decimal("1"),
        spread_usd=Decimal("0"),
        slippage_usd=Decimal("0"),
    )

    assert outcome == TradeOutcome.TRAIL
    assert pnl_pct == pytest.approx(0.20)


def test_signal_mode_simulation_returns_account_return_not_price_return():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_context = _make_h1_context(start, count=20, close=3300.0, high=3300.0, low=3300.0)
    m15_candles = [
        make_candle(start + timedelta(hours=20), close=3310, high=3311, low=3299),
        make_candle(start + timedelta(hours=20, minutes=15), close=3310, high=3311, low=3309),
    ]

    outcome, pnl_pct = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("3300"),
        sl=Decimal("3290"),
        tp1=Decimal("3310"),
        tp2=Decimal("3310"),
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("0.10"),
        equity_at_open=Decimal("10000"),
        contract_size=Decimal("100"),
        spread_usd=Decimal("0"),
        slippage_usd=Decimal("0"),
    )

    assert outcome == TradeOutcome.TP2
    assert pnl_pct == pytest.approx(0.01)
    price_return = float((Decimal("3310") - Decimal("3300")) / Decimal("3300"))
    assert pnl_pct != pytest.approx(price_return)


def test_signal_mode_simulation_applies_spread_and_slippage_to_entry_and_exit():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_context = _make_h1_context(start, count=20, close=3300.0, high=3300.0, low=3300.0)
    m15_candles = [
        make_candle(start + timedelta(hours=20), close=3310, high=3311, low=3299),
        make_candle(start + timedelta(hours=20, minutes=15), close=3310, high=3311, low=3309),
    ]

    _, pnl_pct = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("3300"),
        sl=Decimal("3290"),
        tp1=Decimal("3310"),
        tp2=Decimal("3310"),
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("0.10"),
        equity_at_open=Decimal("10000"),
        contract_size=Decimal("100"),
        spread_usd=Decimal("0.30"),
        slippage_usd=Decimal("0.10"),
    )

    assert pnl_pct == pytest.approx(0.0095)


def test_signal_mode_simulation_returns_quantized_accounting_details():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_context = _make_h1_context(start, count=20, close=100.0, high=101.0, low=99.0)
    m15_candles = [
        make_candle(start + timedelta(hours=20), close=99.9498, high=100, low=99.94),
    ]

    result = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("100"),
        sl=Decimal("99.9498"),
        tp1=Decimal("110"),
        tp2=None,
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("0.20"),
        equity_at_open=Decimal("100"),
        contract_size=Decimal("100"),
        spread_usd=Decimal("0"),
        slippage_usd=Decimal("0"),
        opened_at=start + timedelta(hours=19),
        trade_expiry_hours=72,
    )

    outcome, pnl_pct = result
    assert outcome == TradeOutcome.SL
    assert pnl_pct == pytest.approx(-0.01)
    assert result.pnl_usd == Decimal("-1.00")
    assert result.pnl_pct == Decimal("-0.01000")
    assert result.closed_at == start + timedelta(hours=20)


def test_signal_mode_simulation_expires_before_touch_checks_at_candle_close():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    opened_at = start + timedelta(hours=19)
    expiry_candle_ts = opened_at + timedelta(hours=2)
    h1_context = _make_h1_context(start, count=24, close=100.0, high=101.0, low=99.0)
    m15_candles = [
        make_candle(expiry_candle_ts, close=101, high=111, low=89),
    ]

    result = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("100"),
        sl=Decimal("90"),
        tp1=Decimal("110"),
        tp2=None,
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("1"),
        equity_at_open=Decimal("10000"),
        contract_size=Decimal("100"),
        spread_usd=Decimal("0"),
        slippage_usd=Decimal("0"),
        opened_at=opened_at,
        trade_expiry_hours=2,
    )

    outcome, pnl_pct = result
    assert outcome == TradeOutcome.EXPIRED
    assert result.closed_at == expiry_candle_ts
    assert result.pnl_usd == Decimal("100.00")
    assert pnl_pct == pytest.approx(0.01)


def test_signal_mode_simulation_zero_size_or_equity_returns_zero():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_context = _make_h1_context(start, count=20, close=3300.0, high=3301.0, low=3299.0)
    m15_candles = [
        make_candle(start + timedelta(hours=20), close=3310, high=3311, low=3299),
    ]

    _, no_size = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("3300"),
        sl=Decimal("3290"),
        tp1=Decimal("3310"),
        tp2=None,
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("0"),
        equity_at_open=Decimal("10000"),
        contract_size=Decimal("100"),
        spread_usd=Decimal("0.30"),
        slippage_usd=Decimal("0.10"),
    )
    _, no_equity = simulate_signal_mode_trade_outcome(
        direction="BUY",
        entry=Decimal("3300"),
        sl=Decimal("3290"),
        tp1=Decimal("3310"),
        tp2=None,
        subsequent_m15_candles=m15_candles,
        h1_candles=h1_context,
        size_lots=Decimal("0.10"),
        equity_at_open=Decimal("0"),
        contract_size=Decimal("100"),
        spread_usd=Decimal("0.30"),
        slippage_usd=Decimal("0.10"),
    )

    assert no_size == 0.0
    assert no_equity == 0.0
