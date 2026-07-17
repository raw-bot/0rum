from __future__ import annotations

import math

import pytest

from scripts.research_eth_edge import (
    DAY_MS,
    build_research_report,
    entry_conditions_at,
    evaluate_promotion,
    return_portability,
    simulate_eth_challenger,
    walk_forward_report,
)


def _candles(closes: list[float], *, ranges: list[float] | None = None) -> list[dict]:
    ranges = ranges or [2.0] * len(closes)
    out = []
    for day, (close, span) in enumerate(zip(closes, ranges, strict=True)):
        out.append({
            "ts": day * DAY_MS,
            "open": close,
            "high": close + span / 2.0,
            "low": close - span / 2.0,
            "close": close,
            "volume": 1.0,
        })
    return out


def _baseline() -> tuple[list[dict], list[dict]]:
    eth_closes = [100.0 + 0.2 * day for day in range(120)]
    eth_ranges = [2.0] * 100 + [8.0] * 20
    btc_closes = [100.0] * 120
    return _candles(eth_closes, ranges=eth_ranges), _candles(btc_closes)


def test_entry_conditions_expose_all_four_frozen_gates():
    eth, btc = _baseline()

    conditions = entry_conditions_at(eth, btc, 105)

    assert conditions == {
        "breakout_20": True,
        "atr_expanding": True,
        "trend_100": True,
        "relative_strength_50": True,
    }


def test_entry_conditions_can_reject_each_gate_independently():
    eth, btc = _baseline()

    no_breakout = [dict(candle) for candle in eth]
    no_breakout[105]["close"] = no_breakout[104]["close"]
    no_breakout[105]["open"] = no_breakout[105]["close"]
    assert entry_conditions_at(no_breakout, btc, 105)["breakout_20"] is False

    constant_atr = _candles([100.0 + 0.2 * day for day in range(120)])
    assert entry_conditions_at(constant_atr, btc, 105)["atr_expanding"] is False

    no_trend = _candles([120.0 - 0.1 * day for day in range(120)], ranges=[8.0] * 120)
    assert entry_conditions_at(no_trend, btc, 105)["trend_100"] is False

    fast_btc = _candles([100.0 + 0.5 * day for day in range(120)])
    assert entry_conditions_at(eth, fast_btc, 105)["relative_strength_50"] is False


def test_challenger_fills_entry_and_exit_at_next_open_with_costs():
    eth, btc = _baseline()
    for index in range(106, 110):
        eth[index]["close"] += 2.0
        eth[index]["open"] = eth[index]["close"]
        eth[index]["high"] = eth[index]["close"] + 4.0
        eth[index]["low"] = eth[index]["close"] - 4.0
    eth[110].update({"open": 80.0, "high": 84.0, "low": 76.0, "close": 80.0})
    eth[111].update({"open": 79.0, "high": 83.0, "low": 75.0, "close": 79.0})

    trades = simulate_eth_challenger(eth, btc)

    assert len(trades) == 1
    trade = trades[0]
    assert trade["signal_index"] == 105
    assert trade["entry_index"] == 106
    assert trade["exit_signal_index"] == 110
    assert trade["exit_index"] == 111
    assert trade["entry_price"] == pytest.approx(eth[106]["open"] * 1.0003)
    assert trade["exit_price"] == pytest.approx(eth[111]["open"] * 0.9997)
    fee_move = 0.001 * (trade["entry_price"] + trade["exit_price"]) / 2.0
    assert trade["r"] == pytest.approx(
        (trade["exit_price"] - trade["entry_price"] - fee_move) / trade["risk_distance"]
    )


def test_challenger_does_not_force_close_terminal_position():
    eth, btc = _baseline()

    trades = simulate_eth_challenger(eth[:110], btc[:110])

    assert trades == []


def test_return_portability_uses_only_common_timestamps():
    primary = _candles([100.0, 101.0, 99.0, 102.0, 104.0])
    reference = _candles([200.0, 202.0, 198.0, 204.0, 208.0])
    reference.pop(2)

    result = return_portability(primary, reference)

    assert result["common_bars"] == 4
    assert result["return_observations"] == 3
    assert result["correlation"] == pytest.approx(1.0)
    assert result["median_abs_return_difference"] == pytest.approx(0.0)
    assert result["passed"] is True


def test_walk_forward_assigns_trades_by_entry_signal_to_non_overlapping_windows():
    candles = _candles([100.0] * 1_460)
    trades = [
        {"signal_index": 729, "entry_index": 730, "exit_index": 731, "r": 9.0},
        {"signal_index": 730, "entry_index": 731, "exit_index": 732, "r": 1.0},
        {"signal_index": 1_094, "entry_index": 1_095, "exit_index": 1_096, "r": -0.5},
        {"signal_index": 1_095, "entry_index": 1_096, "exit_index": 1_097, "r": 2.0},
    ]

    report = walk_forward_report(trades, candles, warmup_bars=730, test_bars=365)

    assert [window["trades"] for window in report["windows"]] == [2, 1]
    assert report["aggregate"]["trades"] == 3
    assert report["aggregate"]["net_r"] == pytest.approx(2.5)
    assert report["positive_window_fraction"] == pytest.approx(1.0)


def test_promotion_requires_every_pre_registered_gate():
    aggregate = {
        "trades": 20,
        "profit_factor": 1.5,
        "expectancy_r": 0.20,
        "max_realized_drawdown_r": 3.0,
    }
    benchmark = {"expectancy_r": 0.10}
    portability = {"BTC/EUR": {"passed": True}, "ETH/EUR": {"passed": True}}

    passed = evaluate_promotion(aggregate, benchmark, 0.60, portability)
    failed = evaluate_promotion({**aggregate, "trades": 19}, benchmark, 0.60, portability)

    assert passed["eligible"] is True
    assert all(passed["gates"].values())
    assert failed["eligible"] is False
    assert failed["gates"]["minimum_20_trades"] is False


def test_portability_rejects_constant_or_insufficient_returns():
    constant = _candles([100.0, 100.0, 100.0])

    result = return_portability(constant, constant)

    assert result["passed"] is False
    assert math.isnan(result["correlation"])


def test_build_research_report_compares_frozen_models_without_auto_promotion():
    eth_closes = [100.0 + 0.03 * day + 4.0 * math.sin(day / 25.0) for day in range(1_460)]
    btc_closes = [100.0 + 0.02 * day + 2.0 * math.sin(day / 31.0) for day in range(1_460)]
    eth = _candles(eth_closes, ranges=[4.0 + (day % 20) * 0.1 for day in range(1_460)])
    btc = _candles(btc_closes, ranges=[3.0] * 1_460)

    report = build_research_report(
        {"BTC/EUR": btc, "ETH/EUR": eth},
        {"BTC/EUR": btc[-720:], "ETH/EUR": eth[-720:]},
        retrieved_at="2026-07-11T00:00:00+00:00",
    )

    assert report["retrieved_at"] == "2026-07-11T00:00:00+00:00"
    assert report["history"]["common_bars"] == 1_460
    assert len(report["challenger"]["windows"]) == 2
    assert len(report["benchmark"]["windows"]) == 2
    assert report["portability"]["BTC/EUR"]["passed"] is True
    assert report["portability"]["ETH/EUR"]["passed"] is True
    assert report["promotion"]["eligible"] is False
