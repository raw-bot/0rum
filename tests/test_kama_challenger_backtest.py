import pytest
from datetime import datetime, timezone

from orum.strategies.kama_squeeze import KamaSqueezeParams, KamaSqueezeState
from scripts.backtest_kama_challenger import (
    AkEntryPlan,
    AkRunnerState,
    advance_ak_runner,
    size_notional,
    simulate_kama,
    simulate_ak_plans,
    simulate_ak_macd,
    chronological_metrics,
)


def _candles() -> list[dict]:
    closes = [100.0, 105.0, 110.0, 99.0, 98.0]
    candles = [
        {
            "ts": i,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1.0,
        }
        for i, close in enumerate(closes)
    ]
    candles[2]["open"] = 106.0
    candles[4]["open"] = 98.5
    return candles


def _state() -> KamaSqueezeState:
    return KamaSqueezeState(
        closes=[100.0, 105.0, 110.0, 99.0, 98.0],
        atr=[2.0] * 5,
        efficiency_ratio=[0.1, 0.30, 0.30, 0.30, 0.30],
        kama=[99.0, 103.0, 104.0, 98.0, 97.0],
        squeeze_on=[True, False, False, False, False],
        squeeze_release=[False, True, False, False, False],
        momentum=[-1.0, 1.0, 2.0, 0.5, 0.4],
    )


def test_size_notional_respects_half_percent_stop_risk():
    notional = size_notional(
        equity=10_000.0,
        entry_price=100.0,
        signal_atr=2.0,
        params=KamaSqueezeParams(),
    )

    planned_loss = notional / 100.0 * (2.8 * 2.0)
    assert planned_loss == pytest.approx(50.0)


def test_size_notional_never_exceeds_ten_percent_of_equity():
    notional = size_notional(
        equity=10_000.0,
        entry_price=100.0,
        signal_atr=0.1,
        params=KamaSqueezeParams(),
    )

    assert notional == 1_000.0


def test_simulator_fills_entry_and_confirmed_exit_only_at_following_bar_open():
    result = simulate_kama(
        _candles(),
        state=_state(),
        starting_equity=10_000.0,
        fee_round_trip=0.0,
        slippage_fraction=0.0,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade["signal_index"] == 1
    assert trade["entry_index"] == 2
    assert trade["entry_price"] == 106.0
    assert trade["exit_signal_index"] == 3
    assert trade["exit_index"] == 4
    assert trade["exit_price"] == 98.5


def test_simulator_trade_records_obey_risk_and_allocation_caps():
    result = simulate_kama(
        _candles(),
        state=_state(),
        starting_equity=10_000.0,
        fee_round_trip=0.0,
        slippage_fraction=0.0,
    )

    trade = result.trades[0]
    assert trade["notional_usd"] <= 1_000.0
    assert trade["planned_risk_usd"] <= 50.0


def test_ak_runner_uses_prior_bar_peak_before_testing_current_bar_stop():
    runner = AkRunnerState(entry_price=100.0, risk_distance=10.0, stop=90.0, peak=100.0)

    runner, exit_price = advance_ak_runner(
        runner, high=130.0, low=100.0, atr=5.0, slippage_fraction=0.0,
    )
    assert exit_price is None
    assert runner.peak == 130.0
    assert runner.stop == 90.0

    runner, exit_price = advance_ak_runner(
        runner, high=125.0, low=114.0, atr=5.0, slippage_fraction=0.0,
    )
    assert runner.stop == 117.5
    assert exit_price == 117.5


def test_ak_entry_plan_also_fills_on_the_bar_after_its_confirmed_signal():
    candles = _candles()
    candles[3]["low"] = 1.0
    plan = AkEntryPlan(
        signal_index=1,
        baseline_at_signal=103.0,
        recent_low=99.0,
        recent_high=106.0,
    )

    result = simulate_ak_plans(
        candles,
        plans=[plan],
        atr=[2.0] * len(candles),
        starting_equity=10_000.0,
        fee_round_trip=0.0,
        slippage_fraction=0.0,
    )

    assert len(result.trades) == 1
    assert result.trades[0]["signal_index"] == 1
    assert result.trades[0]["entry_index"] == 2
    assert result.trades[0]["entry_price"] == 106.0


def test_ak_comparator_is_offline_and_returns_no_fabricated_trades_on_short_history():
    result = simulate_ak_macd(_candles())

    assert result.trades == []
    assert result.final_equity == 10_000.0


def test_chronological_metrics_keep_the_holdout_separate():
    candles = [
        {"ts": int(datetime(2023, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)},
        {"ts": int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)},
    ]
    trades = [
        {"signal_index": 0, "r": 1.0},
        {"signal_index": 1, "r": -0.5},
    ]

    report = chronological_metrics(trades, candles, split_year=2024)

    assert report["in_sample"]["trades"] == 1
    assert report["in_sample"]["expectancy_r"] == 1.0
    assert report["out_of_sample"]["trades"] == 1
    assert report["out_of_sample"]["expectancy_r"] == -0.5
    assert set(report["by_year"]) == {"2023", "2024"}
