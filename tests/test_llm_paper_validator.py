from datetime import UTC, datetime, timedelta

from orum.llm.contracts import ProposedDecision
from orum.llm.leverage import apply_leverage_policy
from orum.llm.paper_contracts import LlmPaperAccount
from orum.llm.paper_validator import PaperDecisionValidator


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _decision(**overrides):
    value = {
        "decision_id": "dec-1", "created_at": NOW.isoformat(),
        "lane": "llm_reference", "symbol": "BTC/USDT", "horizon": "4h",
        "action": "open_long", "equity_fraction": 0.1, "requested_leverage": 20,
        "order_type": "market", "limit_price": None, "stop_loss": 97_000,
        "take_profits": [{"price": 105_000, "fraction": 1}],
        "trailing_stop_pct": None, "time_exit_minutes": 240, "confidence": 0.7,
        "thesis": "Absorption may squeeze shorts", "counter_thesis": "Funding is crowded",
        "risk_rationale": "Defined paper loss", "invalidation": "Close below 97000",
        "memo_fr": "J'ouvre un long paper avec un stop défini.",
        "evidence_ids": [], "lesson_ids": [],
    }
    value.update(overrides)
    return ProposedDecision.from_mapping(value)


def _account(processed=()):
    return LlmPaperAccount(
        lane="llm_reference", starting_balance_usd=10_000, balance_usd=10_000,
        processed_decision_ids=processed,
    )


def _leverage():
    return apply_leverage_policy(
        requested=20, paper_min=1, paper_max=40, jurisdiction_profile="fr_retail",
        asset_class="crypto", product_kind="perpetual",
    )


def test_valid_entry_returns_a_structured_acceptance_report():
    report = PaperDecisionValidator().validate(
        decision=_decision(), account=_account(), leverage=_leverage(),
        market_price=100_000, snapshot_cutoff=NOW, now=NOW + timedelta(minutes=1),
    )

    assert report.accepted is True
    assert report.reasons == ()
    assert report.decision_id == "dec-1"
    assert report.paper_effective_leverage == 20
    assert report.fr_retail_eligible_leverage == 2


def test_validator_accumulates_state_freshness_geometry_and_duplicate_reasons():
    report = PaperDecisionValidator(max_snapshot_age=timedelta(minutes=30)).validate(
        decision=_decision(stop_loss=101_000),
        account=_account(processed=("dec-1",)), leverage=_leverage(),
        market_price=100_000, snapshot_cutoff=NOW - timedelta(hours=2), now=NOW,
    )

    assert report.accepted is False
    assert set(report.reasons) >= {
        "decision_already_processed", "snapshot_stale", "long_stop_not_below_market",
    }


def test_validator_rejects_action_that_disagrees_with_position_state():
    close = _decision(
        action="close", equity_fraction=0, requested_leverage=0,
        stop_loss=None, take_profits=[], memo_fr="Je ferme la position.",
    )
    report = PaperDecisionValidator().validate(
        decision=close, account=_account(), leverage=None,
        market_price=100_000, snapshot_cutoff=NOW, now=NOW,
    )

    assert report.accepted is False
    assert report.reasons == ("position_required_for_action",)


def test_validator_rejects_stop_beyond_liquidation_unless_experiment_allows_it():
    decision = _decision(stop_loss=94_000)
    strict = PaperDecisionValidator().validate(
        decision=decision, account=_account(), leverage=_leverage(),
        market_price=100_000, snapshot_cutoff=NOW, now=NOW,
    )
    destructive = PaperDecisionValidator(allow_stop_beyond_liquidation=True).validate(
        decision=decision, account=_account(), leverage=_leverage(),
        market_price=100_000, snapshot_cutoff=NOW, now=NOW,
    )

    assert "stop_at_or_beyond_liquidation" in strict.reasons
    assert destructive.accepted is True


def test_validator_rejects_limit_order_until_pending_order_book_exists():
    report = PaperDecisionValidator().validate(
        decision=_decision(order_type="limit", limit_price=99_000),
        account=_account(),
        leverage=_leverage(),
        market_price=100_000,
        snapshot_cutoff=NOW,
        now=NOW,
    )

    assert report.accepted is False
    assert report.reasons == ("limit_order_execution_not_installed",)
