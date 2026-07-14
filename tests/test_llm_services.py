from datetime import UTC, datetime
import json

import jsonschema
import pytest

from orum.llm.journal import JsonlJournal
from orum.llm.openrouter import CompletionResult
from orum.llm.services import LlmServiceError, MarketAnalyst, ShadowTrader
from orum.llm.snapshot import MarketSnapshotBuilder
from orum.llm.contracts import Evidence, MarketBrief
from orum.llm.prompts import (
    PROPOSED_DECISION_SCHEMA,
    TRADER_PROMPT_VERSION,
    build_trader_prompt,
)


NOW = "2026-07-13T12:00:00+00:00"


class FakeCompletionClient:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return CompletionResult(
            payload=self.payloads.pop(0),
            model="deepseek/deepseek-v4-pro",
            latency_ms=125.5,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            request_id="gen-1",
        )


def test_trader_schema_requires_trailing_stop_as_decimal_fraction():
    jsonschema.validate(
        instance=_decision(trailing_stop_pct=0.015),
        schema=PROPOSED_DECISION_SCHEMA,
    )

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            instance=_decision(trailing_stop_pct=1.5),
            schema=PROPOSED_DECISION_SCHEMA,
        )

    assert TRADER_PROMPT_VERSION == "shadow-trader-fr-v5"


def _evidence():
    return Evidence.from_mapping(
        {
            "evidence_id": "ev-1",
            "kind": "headline",
            "source": "gdelt_doc_2",
            "observed_at": NOW,
            "published_at": "2026-07-13T11:30:00+00:00",
            "title": "Negative headline fails to push Bitcoin lower",
            "url": "https://example.test/story",
            "payload": {"publisher_domain": "example.test"},
            "untrusted_text": True,
        }
    )


def _snapshot(*, paper_account=None):
    candle = {
        "ts": int(datetime(2026, 7, 13, 11, 30, tzinfo=UTC).timestamp() * 1000),
        "open": 100_000,
        "high": 101_000,
        "low": 99_500,
        "close": 100_800,
        "volume": 100,
    }
    return MarketSnapshotBuilder(
        clock=lambda: datetime(2026, 7, 13, 12, 1, tzinfo=UTC)
    ).build(
        cutoff=datetime(2026, 7, 13, 12, tzinfo=UTC),
        symbol="BTC/USDT",
        candles={"15m": [candle]},
        indicators={"15m": {"rsi": 55, "atr": 500}},
        derivatives={
            "status": "available",
            "funding_rate": 0.0001,
            "open_interest_amount": 20_000,
        },
        macro={"dxy_change_pct": -0.2},
        onchain={},
        evidence=[_evidence()],
        paper_account=paper_account or {"equity_usd": 10_000, "positions": []},
    )


def test_trader_prompt_exposes_only_the_active_lane_account_as_authoritative():
    snapshot = _snapshot(
        paper_account={
            "status": "available",
            "native": {"positions": [{"position_id": "native-long"}]},
            "llm_accounts": {
                "llm_reference": {
                    "lane": "llm_reference",
                    "balance_usd": 10_000,
                    "positions": [],
                },
                "llm_evolving": {
                    "lane": "llm_evolving",
                    "balance_usd": 9_500,
                    "positions": [{"position_id": "evolving-short"}],
                },
            },
        }
    )
    brief = MarketBrief.from_mapping(
        _brief(
            snapshot_id=snapshot.snapshot_id,
            memo_fr="La position native longue est sous pression.",
        )
    )

    prompt = build_trader_prompt(
        snapshot,
        brief,
        lane="llm_reference",
        lessons=[],
        paper_min_leverage=1,
        paper_max_leverage=40,
    )
    payload = json.loads(prompt.user)
    account = payload["snapshot"]["paper_account"]

    assert account == {
        "status": "available",
        "active_lane": "llm_reference",
        "active_lane_account": {
            "lane": "llm_reference",
            "balance_usd": 10_000,
            "positions": [],
        },
    }
    assert "native-long" not in prompt.user
    assert "evolving-short" not in prompt.user
    assert "seul active_lane_account" in prompt.system.lower()
    assert "ignore toute position" in prompt.system.lower()


def _brief(**overrides):
    value = {
        "brief_id": "brief-1",
        "created_at": NOW,
        "snapshot_id": _snapshot().snapshot_id,
        "bias": "bullish",
        "regime": "high_volatility_range",
        "horizons": ["15m", "4h"],
        "facts": ["Funding is positive", "Price absorbed a negative headline"],
        "evidence_completeness": 0.8,
        "evidence_freshness": "fresh",
        "narrative_vs_price": "absorption_bullish",
        "interpretation": "Sellers failed to gain control",
        "pain_trade": "A squeeze through the range high",
        "main_scenario": "Breakout after consolidation",
        "alternate_scenarios": ["Failed breakout and range continuation"],
        "catalysts": ["US inflation release"],
        "confidence": 0.72,
        "invalidation": "A closed 1h candle below the range low",
        "memo_fr": "Le marché absorbe la mauvaise nouvelle sans céder.",
        "evidence_ids": ["ev-1"],
    }
    value.update(overrides)
    return value


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
        "confidence": 0.76,
        "thesis": "Absorption plus improving structure can squeeze late shorts",
        "counter_thesis": "Positive funding means longs may already be crowded",
        "risk_rationale": "A wide structural stop tolerates range volatility",
        "invalidation": "A closed 1h candle below 99000",
        "memo_fr": "J'achète le breakout avec 20x en paper et une invalidation nette.",
        "evidence_ids": ["ev-1"],
        "lesson_ids": [],
    }
    value.update(overrides)
    return value


def test_analyst_and_trader_produce_visible_journaled_reasoning(tmp_path):
    snapshot = _snapshot()
    client = FakeCompletionClient(_brief(), _decision())
    brief_journal = JsonlJournal(tmp_path / "briefs.jsonl")
    decision_journal = JsonlJournal(tmp_path / "decisions.jsonl")
    analyst = MarketAnalyst(
        client=client,
        journal=brief_journal,
        clock=lambda: datetime(2026, 7, 13, 12, 2, tzinfo=UTC),
    )
    trader = ShadowTrader(
        client=client,
        journal=decision_journal,
        paper_min_leverage=1,
        paper_max_leverage=40,
        jurisdiction_profile="fr_retail",
        clock=lambda: datetime(2026, 7, 13, 12, 3, tzinfo=UTC),
    )

    brief = analyst.analyze(snapshot)
    result = trader.decide(snapshot, brief, lane="llm_reference", lessons=[])

    assert brief.facts
    assert brief.interpretation
    assert brief.main_scenario
    assert brief.alternate_scenarios
    assert brief.pain_trade
    assert brief.catalysts
    assert brief.invalidation
    assert result.decision.requested_leverage == 20
    assert result.decision.counter_thesis
    assert result.decision.memo_fr.startswith("J'achète")
    assert result.leverage.paper_effective == 20
    assert result.leverage.fr_retail_eligible == 2
    assert brief_journal.read()[0]["status"] == "valid"
    decision_record = decision_journal.read()[0]
    assert decision_record["status"] == "valid"
    assert decision_record["requested_leverage"] == 20
    assert decision_record["paper_effective_leverage"] == 20
    assert decision_record["fr_retail_eligible_leverage"] == 2
    assert decision_record["snapshot_hash"] == snapshot.content_hash
    assert decision_record["snapshot_cutoff"] == snapshot.cutoff.isoformat()
    assert decision_record["market_price"] == 100_800
    assert decision_record["candle_ts"] == snapshot.candles["15m"][-1]["ts"]
    analyst_prompt, trader_prompt = client.calls
    assert "pain trade" in analyst_prompt["system"].lower()
    assert "texte non fiable" in analyst_prompt["system"].lower()
    assert "exclusivement en français" in analyst_prompt["system"].lower()
    assert "perte" in trader_prompt["system"].lower()
    assert "liquidation" in trader_prompt["system"].lower()
    assert "levier" in trader_prompt["system"].lower()
    assert "exclusivement en français" in trader_prompt["system"].lower()
    assert "stop_loss=null" in trader_prompt["system"].lower()
    assert trader_prompt["schema_name"] == "proposed_decision"


def test_unknown_evidence_id_is_rejected_and_journaled_as_model_error(tmp_path):
    snapshot = _snapshot()
    journal = JsonlJournal(tmp_path / "briefs.jsonl")
    analyst = MarketAnalyst(
        client=FakeCompletionClient(_brief(evidence_ids=["ev-invented"])),
        journal=journal,
        clock=lambda: datetime(2026, 7, 13, 12, 2, tzinfo=UTC),
    )

    with pytest.raises(LlmServiceError, match="unknown evidence"):
        analyst.analyze(snapshot)

    record = journal.read()[0]
    assert record["status"] == "model_error"
    assert record["error_type"] == "LlmServiceError"
    assert record["raw_payload"]["evidence_ids"] == ["ev-invented"]


def test_analyst_replaces_model_timestamp_with_snapshot_cutoff(tmp_path):
    snapshot = _snapshot()
    analyst = MarketAnalyst(
        client=FakeCompletionClient(_brief(created_at="2099-01-01T00:00:00")),
        journal=JsonlJournal(tmp_path / "briefs.jsonl"),
    )

    brief = analyst.analyze(snapshot)

    assert brief.created_at == snapshot.cutoff
    assert analyst.journal.read()[0]["brief"]["created_at"] == snapshot.cutoff.isoformat()


def test_trader_rejects_wrong_lane_symbol_or_unavailable_lesson(tmp_path):
    snapshot = _snapshot()
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    brief = MarketAnalyst(
        client=FakeCompletionClient(_brief()),
        journal=JsonlJournal(tmp_path / "briefs.jsonl"),
    ).analyze(snapshot)

    for overrides, message in [
        ({"lane": "llm_evolving"}, "lane"),
        ({"symbol": "ETH/USDT"}, "symbol"),
        ({"lesson_ids": ["lesson-invented"]}, "lesson"),
    ]:
        trader = ShadowTrader(
            client=FakeCompletionClient(_decision(**overrides)),
            journal=journal,
        )
        with pytest.raises(LlmServiceError, match=message):
            trader.decide(snapshot, brief, lane="llm_reference", lessons=[])

    assert [record["status"] for record in journal.read()] == [
        "model_error",
        "model_error",
        "model_error",
    ]


def test_hold_is_a_complete_verbal_decision_without_leverage_policy_call(tmp_path):
    snapshot = _snapshot()
    brief = MarketAnalyst(
        client=FakeCompletionClient(_brief()),
        journal=JsonlJournal(tmp_path / "briefs.jsonl"),
    ).analyze(snapshot)
    hold = _decision(
        action="hold",
        equity_fraction=0,
        requested_leverage=0,
        stop_loss=None,
        take_profits=[],
        time_exit_minutes=None,
        thesis="No clean edge inside the range",
        invalidation="A closed breakout would change the decision",
        memo_fr="Je reste à l'écart jusqu'à une cassure confirmée.",
    )
    trader = ShadowTrader(
        client=FakeCompletionClient(hold),
        journal=JsonlJournal(tmp_path / "decisions.jsonl"),
    )

    result = trader.decide(snapshot, brief, lane="llm_reference", lessons=[])

    assert result.decision.action == "hold"
    assert result.leverage is None
    record = trader.journal.read()[0]
    assert record["paper_effective_leverage"] is None
    assert record["decision"]["memo_fr"].startswith("Je reste")


def test_trader_replaces_model_id_and_timestamp_with_server_canonical_values(tmp_path):
    snapshot = _snapshot()
    brief = MarketAnalyst(
        client=FakeCompletionClient(_brief()),
        journal=JsonlJournal(tmp_path / "briefs.jsonl"),
    ).analyze(snapshot)
    payload = _decision(
        decision_id="model-reused-id",
        created_at="2099-01-01T00:00:00+00:00",
    )
    trader = ShadowTrader(
        client=FakeCompletionClient(payload),
        journal=JsonlJournal(tmp_path / "decisions.jsonl"),
    )

    first = trader.decide(snapshot, brief, lane="llm_reference", lessons=[]).decision
    second = ShadowTrader(
        client=FakeCompletionClient({**payload, "decision_id": "another-model-id"}),
        journal=JsonlJournal(tmp_path / "decisions-2.jsonl"),
    ).decide(snapshot, brief, lane="llm_reference", lessons=[]).decision

    assert first.decision_id.startswith("decision-")
    assert first.decision_id != "model-reused-id"
    assert first.decision_id == second.decision_id
    assert first.created_at == snapshot.cutoff


def test_analyst_rejects_cjk_memo_and_journals_raw_payload_as_model_error(tmp_path):
    snapshot = _snapshot()
    journal = JsonlJournal(tmp_path / "briefs.jsonl")
    analyst = MarketAnalyst(
        client=FakeCompletionClient(_brief(memo_fr="Le marché reste calme 〇.")),
        journal=journal,
    )

    with pytest.raises(LlmServiceError, match="response_not_french"):
        analyst.analyze(snapshot)

    records = journal.read()
    assert len(records) == 1
    assert records[0]["status"] == "model_error"
    assert records[0]["brief"] is None
    assert records[0]["raw_payload"]["memo_fr"] == "Le marché reste calme 〇."


def test_analyst_rejects_kana_memo_and_journals_model_error(tmp_path):
    snapshot = _snapshot()
    journal = JsonlJournal(tmp_path / "briefs.jsonl")
    analyst = MarketAnalyst(
        client=FakeCompletionClient(_brief(memo_fr="Le marché reste calme カ.")),
        journal=journal,
    )

    with pytest.raises(LlmServiceError, match="response_not_french"):
        analyst.analyze(snapshot)

    records = journal.read()
    assert [record["status"] for record in records] == ["model_error"]
    assert records[0]["brief"] is None


@pytest.mark.parametrize(
    "character",
    [
        "\u3005",
        "\u302e",
        "\u3031",
        "\u3131",
        "\U0001aff0",
        "\U00030000",
        "\U00031350",
        "\U000323af",
    ],
)
def test_analyst_rejects_cjk_symbols_and_kana_extensions(tmp_path, character):
    snapshot = _snapshot()
    journal = JsonlJournal(tmp_path / "briefs.jsonl")
    analyst = MarketAnalyst(
        client=FakeCompletionClient(_brief(memo_fr=f"Le marché reste calme {character}.")),
        journal=journal,
    )

    with pytest.raises(LlmServiceError, match="response_not_french"):
        analyst.analyze(snapshot)

    records = journal.read()
    assert [record["status"] for record in records] == ["model_error"]
    assert records[0]["brief"] is None


def test_trader_rejects_cjk_memo_and_only_journals_model_error(tmp_path):
    snapshot = _snapshot()
    brief = MarketAnalyst(
        client=FakeCompletionClient(_brief()),
        journal=JsonlJournal(tmp_path / "briefs.jsonl"),
    ).analyze(snapshot)
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    trader = ShadowTrader(
        client=FakeCompletionClient(_decision(memo_fr="J'achète le breakout 漢.")),
        journal=journal,
    )

    with pytest.raises(LlmServiceError, match="response_not_french"):
        trader.decide(snapshot, brief, lane="llm_reference", lessons=[])

    records = journal.read()
    assert [record["status"] for record in records] == ["model_error"]
    assert records[0]["decision"] is None
    assert records[0]["raw_payload"]["memo_fr"] == "J'achète le breakout 漢."


def test_trader_rejects_halfwidth_hangul_memo_and_journals_model_error(tmp_path):
    snapshot = _snapshot()
    brief = MarketAnalyst(
        client=FakeCompletionClient(_brief()),
        journal=JsonlJournal(tmp_path / "briefs.jsonl"),
    ).analyze(snapshot)
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    trader = ShadowTrader(
        client=FakeCompletionClient(_decision(memo_fr="Le marché reste calme \uffa0.")),
        journal=journal,
    )

    with pytest.raises(LlmServiceError, match="response_not_french"):
        trader.decide(snapshot, brief, lane="llm_reference", lessons=[])

    records = journal.read()
    assert [record["status"] for record in records] == ["model_error"]
    assert records[0]["decision"] is None
