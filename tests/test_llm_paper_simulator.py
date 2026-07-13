from datetime import UTC, datetime

import pytest

from orum.llm.contracts import ProposedDecision
from orum.llm.leverage import apply_leverage_policy
from orum.llm.paper_contracts import LlmPaperAccount
from orum.llm.paper_simulator import LlmPaperSimulator, SimulatorError


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _account(lane="llm_reference"):
    return LlmPaperAccount(
        lane=lane,
        starting_balance_usd=10_000,
        balance_usd=10_000,
    )


def _decision(**overrides):
    value = {
        "decision_id": "dec-1",
        "created_at": NOW.isoformat(),
        "lane": "llm_reference",
        "symbol": "BTC/USDT",
        "horizon": "4h",
        "action": "open_long",
        "equity_fraction": 0.1,
        "requested_leverage": 20,
        "order_type": "market",
        "limit_price": None,
        "stop_loss": 97_000,
        "take_profits": [
            {"price": 105_000, "fraction": 0.5},
            {"price": 110_000, "fraction": 0.5},
        ],
        "trailing_stop_pct": None,
        "time_exit_minutes": None,
        "confidence": 0.7,
        "thesis": "Absorption can squeeze shorts",
        "counter_thesis": "Funding is already crowded",
        "risk_rationale": "Paper experiment with explicit liquidation",
        "invalidation": "Closed candle below 97000",
        "memo_fr": "Long agressif en paper avec invalidation nette.",
        "evidence_ids": [],
        "lesson_ids": [],
    }
    value.update(overrides)
    return ProposedDecision.from_mapping(value)


def _leverage(requested=20, paper_max=40):
    return apply_leverage_policy(
        requested=requested,
        paper_min=1,
        paper_max=paper_max,
        jurisdiction_profile="fr_retail",
        asset_class="crypto",
        product_kind="perpetual",
    )


def test_open_20x_long_uses_equity_fraction_as_isolated_margin():
    simulator = LlmPaperSimulator(fee_rate=0.0005, maintenance_margin_rate=0.005)

    result = simulator.apply_decision(
        _account(), _decision(), _leverage(), price=100_000, candle_ts=1_000
    )

    position = result.account.positions["BTC/USDT"]
    assert position.initial_margin_usd == pytest.approx(1_000)
    assert position.notional_usd == pytest.approx(20_000)
    assert position.qty == pytest.approx(0.2)
    assert position.liquidation_px == pytest.approx(95_500)
    assert position.requested_leverage == 20
    assert position.effective_leverage == 20
    assert result.account.balance_usd == pytest.approx(9_990)
    assert result.fills[0].fee_usd == pytest.approx(10)


def test_short_close_realizes_side_correct_profit():
    simulator = LlmPaperSimulator(fee_rate=0, allow_stop_beyond_liquidation=True)
    decision = _decision(
        action="open_short",
        stop_loss=103_000,
        take_profits=[{"price": 95_000, "fraction": 1}],
    )
    opened = simulator.apply_decision(
        _account(), decision, _leverage(), price=100_000, candle_ts=1_000
    )
    closed = simulator.apply_decision(
        opened.account,
        _decision(
            decision_id="dec-close",
            action="close",
            equity_fraction=0,
            requested_leverage=0,
            stop_loss=None,
            take_profits=[],
        ),
        None,
        price=95_000,
        candle_ts=2_000,
    )

    assert closed.account.balance_usd == pytest.approx(11_000)
    assert closed.fills[0].realized_pnl_usd == pytest.approx(1_000)
    assert closed.account.positions == {}


def test_liquidation_precedes_stop_when_same_candle_touches_both():
    simulator = LlmPaperSimulator(fee_rate=0, allow_stop_beyond_liquidation=True)
    decision = _decision(stop_loss=94_000)
    opened = simulator.apply_decision(
        _account(), decision, _leverage(), price=100_000, candle_ts=1_000
    )

    monitored = simulator.monitor_candle(
        opened.account,
        {"ts": 2_000, "open": 100_000, "high": 101_000, "low": 93_000, "close": 94_000},
    )

    assert monitored.fills[0].action == "liquidation"
    assert monitored.fills[0].price == pytest.approx(95_500)
    assert monitored.account.positions == {}


def test_stop_precedes_take_profit_on_ambiguous_same_bar():
    simulator = LlmPaperSimulator(fee_rate=0)
    opened = simulator.apply_decision(
        _account(), _decision(), _leverage(), price=100_000, candle_ts=1_000
    )

    monitored = simulator.monitor_candle(
        opened.account,
        {"ts": 2_000, "open": 100_000, "high": 106_000, "low": 96_000, "close": 104_000},
    )

    assert [fill.action for fill in monitored.fills] == ["stop"]
    assert monitored.fills[0].price == 97_000


def test_two_half_targets_close_exactly_the_initial_quantity():
    simulator = LlmPaperSimulator(fee_rate=0)
    opened = simulator.apply_decision(
        _account(), _decision(), _leverage(), price=100_000, candle_ts=1_000
    )
    first = simulator.monitor_candle(
        opened.account,
        {"ts": 2_000, "open": 100_000, "high": 106_000, "low": 99_000, "close": 105_500},
    )
    assert first.account.positions["BTC/USDT"].qty == pytest.approx(0.1)
    second = simulator.monitor_candle(
        first.account,
        {"ts": 3_000, "open": 106_000, "high": 111_000, "low": 105_000, "close": 110_500},
    )

    assert second.account.positions == {}
    assert sum(fill.qty for fill in first.fills + second.fills) == pytest.approx(0.2)


def test_duplicate_decision_is_rejected_without_mutation():
    simulator = LlmPaperSimulator(fee_rate=0)
    decision = _decision()
    opened = simulator.apply_decision(
        _account(), decision, _leverage(), price=100_000, candle_ts=1_000
    )

    with pytest.raises(SimulatorError, match="already processed"):
        simulator.apply_decision(
            opened.account, decision, _leverage(), price=100_000, candle_ts=1_000
        )


def test_hold_is_recorded_without_fill_or_position():
    simulator = LlmPaperSimulator()
    hold = _decision(
        action="hold",
        equity_fraction=0,
        requested_leverage=0,
        stop_loss=None,
        take_profits=[],
    )

    result = simulator.apply_decision(
        _account(), hold, None, price=100_000, candle_ts=1_000
    )

    assert result.fills == ()
    assert result.account.processed_decision_ids == ("dec-1",)


def test_add_then_reduce_changes_only_the_isolated_lane_position():
    simulator = LlmPaperSimulator(fee_rate=0)
    opened = simulator.apply_decision(
        _account(), _decision(), _leverage(), price=100_000, candle_ts=1_000
    )
    added = simulator.apply_decision(
        opened.account,
        _decision(
            decision_id="dec-add",
            action="add",
            equity_fraction=0.05,
            requested_leverage=10,
            stop_loss=98_000,
            take_profits=[{"price": 108_000, "fraction": 1}],
        ),
        _leverage(requested=10),
        price=102_000,
        candle_ts=2_000,
    )
    assert added.fills[0].action == "add"
    assert added.account.positions["BTC/USDT"].qty > opened.account.positions["BTC/USDT"].qty

    reduced = simulator.apply_decision(
        added.account,
        _decision(
            decision_id="dec-reduce",
            action="reduce",
            equity_fraction=0.5,
            requested_leverage=0,
            stop_loss=None,
            take_profits=[],
        ),
        None,
        price=103_000,
        candle_ts=3_000,
    )
    assert reduced.fills[0].action == "reduce"
    assert reduced.account.positions["BTC/USDT"].qty == pytest.approx(
        added.account.positions["BTC/USDT"].qty / 2
    )
