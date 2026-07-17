import pytest

from scripts.research_eur_universe import chronological_report, simulate_donchian


def _candles(closes: list[float]) -> list[dict]:
    return [
        {
            "ts": i * 86_400_000,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1.0,
        }
        for i, close in enumerate(closes)
    ]


def test_donchian_signal_fills_entry_and_exit_at_following_open():
    closes = [100.0] * 20 + [110.0] + [110.0] * 10 + [90.0, 89.0]
    bars = _candles(closes)
    bars[21]["open"] = 111.0
    bars[32]["open"] = 89.0

    trades = simulate_donchian(bars, fee_rt=0.0, slippage=0.0)

    assert len(trades) == 1
    trade = trades[0]
    assert trade["signal_index"] == 20
    assert trade["entry_index"] == 21
    assert trade["entry_price"] == 111.0
    assert trade["exit_signal_index"] == 31
    assert trade["exit_index"] == 32
    assert trade["exit_price"] == 89.0
    assert trade["r"] < 0


def test_donchian_does_not_force_close_an_open_end_of_data_trade():
    bars = _candles([100.0] * 20 + [110.0, 112.0])

    assert simulate_donchian(bars, fee_rt=0.0, slippage=0.0) == []


def test_chronological_report_excludes_trade_crossing_split_boundary():
    bars = _candles([100.0] * 100)
    trades = [
        {"signal_index": 20, "exit_index": 50, "entry_index": 21, "r": 1.0},
        {"signal_index": 60, "exit_index": 75, "entry_index": 61, "r": 2.0},
        {"signal_index": 80, "exit_index": 90, "entry_index": 81, "r": -0.5},
    ]

    report = chronological_report(trades, bars, split_fraction=0.70)

    assert report["split_index"] == 70
    assert report["train"]["trades"] == 1
    assert report["train"]["expectancy_r"] == 1.0
    assert report["holdout"]["trades"] == 1
    assert report["holdout"]["expectancy_r"] == -0.5
    assert report["excluded_boundary_trades"] == 1


def test_report_flags_short_history_and_small_holdout_as_provisional():
    bars = _candles([100.0] * 720)
    trades = [
        {"signal_index": 600, "exit_index": 620, "entry_index": 601, "r": 1.0},
    ]

    report = chronological_report(trades, bars, split_fraction=0.70)

    assert report["provisional_only"] is True
    assert "history_below_1000_bars" in report["provisional_reasons"]
    assert "holdout_below_20_trades" in report["provisional_reasons"]


def test_simulator_rejects_non_chronological_data():
    bars = _candles([100.0] * 25)
    bars[2]["ts"] = bars[1]["ts"]

    with pytest.raises(ValueError, match="strictly increasing"):
        simulate_donchian(bars)
