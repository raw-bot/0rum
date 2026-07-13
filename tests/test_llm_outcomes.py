from datetime import UTC, datetime

import pytest

from orum.llm.outcomes import OutcomeEvaluator


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def test_long_outcome_computes_mfe_mae_costs_and_counterfactual():
    outcome = OutcomeEvaluator(fee_rate=0.0005).evaluate(
        decision_id="dec-1", lane="llm_reference", symbol="BTC/USDT", side="long",
        entry_price=100, leverage=20, equity_fraction=0.1, confidence=0.7,
        stop_loss=95, take_profit=110, liquidation_price=94,
        candles=[
            {"ts": 1, "open": 100, "high": 106, "low": 98, "close": 104},
            {"ts": 2, "open": 104, "high": 111, "low": 103, "close": 110},
        ],
        evaluated_at=NOW,
    )

    assert outcome.exit_reason == "take_profit"
    assert outcome.exit_price == 110
    assert outcome.mfe_pct == pytest.approx(11)
    assert outcome.mae_pct == pytest.approx(-2)
    assert outcome.net_return_on_margin == pytest.approx(1.979)
    assert outcome.account_return == pytest.approx(0.1979)
    assert outcome.opposite_net_return_on_margin < 0
    assert outcome.hold_return_on_margin == 0


def test_ambiguous_bar_uses_liquidation_then_stop_then_target_priority():
    evaluator = OutcomeEvaluator(fee_rate=0)
    liquidated = evaluator.evaluate(
        decision_id="d1", lane="llm_reference", symbol="BTC/USDT", side="long",
        entry_price=100, leverage=20, equity_fraction=1, confidence=0.8,
        stop_loss=95, take_profit=110, liquidation_price=96,
        candles=[{"ts": 1, "open": 100, "high": 111, "low": 94, "close": 105}],
        evaluated_at=NOW,
    )
    stopped = evaluator.evaluate(
        decision_id="d2", lane="llm_reference", symbol="BTC/USDT", side="long",
        entry_price=100, leverage=2, equity_fraction=1, confidence=0.8,
        stop_loss=95, take_profit=110, liquidation_price=50,
        candles=[{"ts": 1, "open": 100, "high": 111, "low": 94, "close": 105}],
        evaluated_at=NOW,
    )

    assert liquidated.exit_reason == "liquidation"
    assert stopped.exit_reason == "stop"


def test_short_outcome_uses_side_correct_excursions():
    outcome = OutcomeEvaluator(fee_rate=0).evaluate(
        decision_id="d", lane="llm_evolving", symbol="BTC/USDT", side="short",
        entry_price=100, leverage=5, equity_fraction=0.2, confidence=0.6,
        stop_loss=105, take_profit=90, liquidation_price=119,
        candles=[{"ts": 1, "open": 100, "high": 102, "low": 89, "close": 91}],
        evaluated_at=NOW,
    )

    assert outcome.exit_reason == "take_profit"
    assert outcome.mfe_pct == pytest.approx(11)
    assert outcome.mae_pct == pytest.approx(-2)
