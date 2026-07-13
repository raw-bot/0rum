from datetime import UTC, datetime

import pytest

from orum.llm.paper_contracts import (
    LlmPaperAccount,
    LlmPaperFill,
    LlmPaperPosition,
    LlmPaperTarget,
    PaperContractError,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _position(**overrides):
    values = {
        "position_id": "pos-1",
        "decision_id": "dec-1",
        "lane": "llm_reference",
        "symbol": "BTC/USDT",
        "side": "long",
        "qty": 0.2,
        "entry_px": 100_000,
        "mark_px": 101_000,
        "notional_usd": 20_000,
        "initial_margin_usd": 1_000,
        "requested_leverage": 20,
        "effective_leverage": 20,
        "entry_fee_usd": 10,
        "liquidation_px": 95_500,
        "liquidation_formula_version": "isolated_v1",
        "stop_loss": 97_000,
        "take_profits": (
            LlmPaperTarget("tp-1", 105_000, 0.5),
            LlmPaperTarget("tp-2", 110_000, 0.5),
        ),
        "trailing_stop_pct": None,
        "time_exit_at": None,
        "opened_at": NOW,
        "thesis": "Absorption can squeeze shorts",
        "invalidation": "Closed 1h candle below 97000",
    }
    values.update(overrides)
    return LlmPaperPosition(**values)


def test_long_and_short_positions_round_trip_with_full_exit_plan():
    for side, liquidation in (("long", 95_500), ("short", 104_500)):
        original = _position(side=side, liquidation_px=liquidation)

        restored = LlmPaperPosition.from_mapping(original.to_mapping())

        assert restored == original
        assert [target.fraction_remaining for target in restored.take_profits] == [0.5, 0.5]
        assert restored.requested_leverage == 20
        assert restored.effective_leverage == 20


def test_account_equity_uses_side_correct_unrealized_pnl():
    long = _position(position_id="long", decision_id="d-long", side="long", qty=0.1)
    short = _position(
        position_id="short",
        decision_id="d-short",
        side="short",
        qty=0.2,
        entry_px=100_000,
        mark_px=99_000,
        liquidation_px=104_500,
    )
    account = LlmPaperAccount(
        lane="llm_reference",
        starting_balance_usd=10_000,
        balance_usd=9_980,
        positions={"BTC-LONG": long, "BTC-SHORT": short},
        processed_decision_ids=("d-long", "d-short"),
        last_processed_candles={"long": 1_000},
    )

    assert account.equity_usd == pytest.approx(10_280)
    assert LlmPaperAccount.from_mapping(account.to_mapping()) == account


def test_requested_and_effective_leverage_are_independent():
    position = _position(requested_leverage=50, effective_leverage=40)

    assert position.requested_leverage == 50
    assert position.effective_leverage == 40


def test_fill_round_trip_keeps_operation_and_balance_audit():
    fill = LlmPaperFill(
        fill_id="fill-1",
        operation_id="op-1",
        decision_id="dec-1",
        position_id="pos-1",
        lane="llm_reference",
        symbol="BTC/USDT",
        action="open",
        reason="llm_decision",
        side="long",
        qty=0.2,
        price=100_000,
        fee_usd=10,
        realized_pnl_usd=0,
        balance_after_usd=9_990,
        candle_ts=1_000,
        created_at=NOW,
    )

    assert LlmPaperFill.from_mapping(fill.to_mapping()) == fill


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qty", float("nan")),
        ("entry_px", float("inf")),
        ("initial_margin_usd", -1),
        ("effective_leverage", 0),
    ],
)
def test_position_rejects_non_finite_or_mechanically_invalid_values(field, value):
    with pytest.raises(PaperContractError):
        _position(**{field: value})


def test_duplicate_target_ids_and_unknown_fields_are_rejected():
    duplicate_targets = (
        LlmPaperTarget("tp", 105_000, 0.5),
        LlmPaperTarget("tp", 110_000, 0.5),
    )
    with pytest.raises(PaperContractError, match="duplicate"):
        _position(take_profits=duplicate_targets)

    raw = _position().to_mapping()
    raw["secret_extra"] = True
    with pytest.raises(PaperContractError, match="unknown"):
        LlmPaperPosition.from_mapping(raw)


def test_missing_account_state_creates_fresh_lane_account():
    account = LlmPaperAccount.from_mapping(
        None,
        lane="llm_evolving",
        starting_balance_usd=25_000,
    )

    assert account.schema_version == 1
    assert account.lane == "llm_evolving"
    assert account.balance_usd == 25_000
    assert account.positions == {}
