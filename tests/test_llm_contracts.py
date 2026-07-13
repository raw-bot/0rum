from datetime import UTC, datetime

import pytest

from orum.llm.contracts import (
    ContractError,
    Evidence,
    MarketBrief,
    ProposedDecision,
)


NOW = "2026-07-13T12:00:00+00:00"


def _decision(**overrides):
    value = {
        "decision_id": "dec-1",
        "created_at": NOW,
        "lane": "llm_reference",
        "symbol": "BTC/USDT",
        "horizon": "4h",
        "action": "open_long",
        "equity_fraction": 0.25,
        "requested_leverage": 20,
        "order_type": "market",
        "limit_price": None,
        "stop_loss": 99_000,
        "take_profits": [{"price": 105_000, "fraction": 1.0}],
        "trailing_stop_pct": None,
        "time_exit_minutes": 240,
        "confidence": 0.72,
        "thesis": "Breakout supported by flow",
        "counter_thesis": "Funding is crowded",
        "risk_rationale": "Wide stop and event risk",
        "invalidation": "Closed H1 candle below 99000",
        "memo_fr": "Je tente le breakout avec une invalidation nette.",
        "evidence_ids": ["ev-1"],
        "lesson_ids": [],
    }
    value.update(overrides)
    return value


def test_decision_requires_visible_thesis_counter_thesis_and_invalidation():
    decision = ProposedDecision.from_mapping(_decision())

    assert decision.requested_leverage == 20
    assert decision.counter_thesis == "Funding is crowded"
    assert decision.created_at == datetime(2026, 7, 13, 12, tzinfo=UTC)
    assert decision.to_mapping()["take_profits"] == [
        {"price": 105_000.0, "fraction": 1.0}
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"action": "bet_the_farm"}, "action"),
        ({"requested_leverage": -1}, "requested_leverage"),
        ({"requested_leverage": float("inf")}, "requested_leverage"),
        ({"memo_fr": ""}, "memo_fr"),
        ({"counter_thesis": ""}, "counter_thesis"),
        ({"confidence": 1.01}, "confidence"),
        (
            {
                "take_profits": [
                    {"price": 105_000, "fraction": 0.7},
                    {"price": 106_000, "fraction": 0.4},
                ]
            },
            "take_profits",
        ),
        ({"stop_loss": 106_000}, "geometry"),
        ({"order_type": "limit", "limit_price": None}, "limit_price"),
    ],
)
def test_decision_rejects_invalid_mechanics(overrides, message):
    with pytest.raises(ContractError, match=message):
        ProposedDecision.from_mapping(_decision(**overrides))


def test_short_geometry_is_validated_in_the_other_direction():
    decision = ProposedDecision.from_mapping(
        _decision(
            action="open_short",
            stop_loss=106_000,
            take_profits=[{"price": 99_000, "fraction": 1.0}],
        )
    )
    assert decision.action == "open_short"

    with pytest.raises(ContractError, match="geometry"):
        ProposedDecision.from_mapping(
            _decision(
                action="open_short",
                stop_loss=98_000,
                take_profits=[{"price": 99_000, "fraction": 1.0}],
            )
        )


def test_hold_is_complete_but_has_no_trade_geometry():
    decision = ProposedDecision.from_mapping(
        _decision(
            action="hold",
            equity_fraction=0,
            requested_leverage=0,
            stop_loss=None,
            take_profits=[],
            time_exit_minutes=None,
            thesis="No edge while price is trapped in range",
            invalidation="A confirmed range break would change the decision",
            memo_fr="Je reste à l'écart tant que le range tient.",
        )
    )
    assert decision.action == "hold"


def test_evidence_normalizes_utc_and_round_trips_json_primitives():
    evidence = Evidence.from_mapping(
        {
            "evidence_id": "ev-1",
            "kind": "headline",
            "source": "gdelt",
            "observed_at": NOW,
            "published_at": "2026-07-13T11:45:00Z",
            "title": "Rates decision moves markets",
            "url": "https://example.test/story",
            "payload": {"language": "en"},
            "untrusted_text": True,
        }
    )

    assert evidence.published_at == datetime(2026, 7, 13, 11, 45, tzinfo=UTC)
    assert evidence.to_mapping()["observed_at"] == NOW


def test_market_brief_requires_observable_reasoning_and_valid_evidence_ids():
    brief = MarketBrief.from_mapping(
        {
            "brief_id": "brief-1",
            "created_at": NOW,
            "snapshot_id": "snap-1",
            "bias": "uncertain",
            "regime": "high_volatility_range",
            "horizons": ["1h", "4h"],
            "facts": ["Funding is positive", "Price rejected the daily high"],
            "evidence_completeness": 0.75,
            "evidence_freshness": "mixed",
            "narrative_vs_price": "Hawkish headlines, resilient price",
            "interpretation": "Sellers are not yet in control",
            "pain_trade": "A squeeze above the daily high",
            "main_scenario": "Range then upside breakout",
            "alternate_scenarios": ["Failed breakout and return to value"],
            "catalysts": ["US CPI"],
            "confidence": 0.64,
            "invalidation": "Acceptance below range low",
            "memo_fr": "Le marché absorbe les nouvelles négatives.",
            "evidence_ids": ["ev-1"],
        }
    )

    assert brief.bias == "uncertain"
    assert brief.facts[0] == "Funding is positive"
    assert brief.to_mapping()["alternate_scenarios"] == [
        "Failed breakout and return to value"
    ]


@pytest.mark.parametrize("field", ["facts", "interpretation", "pain_trade", "main_scenario", "invalidation", "memo_fr"])
def test_market_brief_rejects_missing_visible_reasoning(field):
    value = {
        "brief_id": "brief-1",
        "created_at": NOW,
        "snapshot_id": "snap-1",
        "bias": "neutral",
        "regime": "range",
        "horizons": ["4h"],
        "facts": ["Price is inside value"],
        "evidence_completeness": 1,
        "evidence_freshness": "fresh",
        "narrative_vs_price": "Aligned",
        "interpretation": "No imbalance",
        "pain_trade": "Breakout",
        "main_scenario": "Range continuation",
        "alternate_scenarios": ["Breakout"],
        "catalysts": [],
        "confidence": 0.5,
        "invalidation": "Close outside range",
        "memo_fr": "Je privilégie l'attente.",
        "evidence_ids": ["ev-1"],
    }
    value[field] = [] if field == "facts" else ""

    with pytest.raises(ContractError, match=field):
        MarketBrief.from_mapping(value)
