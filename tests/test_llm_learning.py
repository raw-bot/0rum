from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from orum.llm.journal import JsonlJournal
from orum.llm.learning import LearningProcessor
from orum.llm.lessons import LessonBook
from orum.llm.outcomes import OutcomeEvaluator
from orum.llm.paper_contracts import LlmPaperFill
from orum.llm.snapshot import MarketSnapshotBuilder


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
OPEN_TS = int((NOW - timedelta(minutes=30)).timestamp() * 1000)
CLOSE_TS = int((NOW - timedelta(minutes=15)).timestamp() * 1000)


def _fill(action, ts, price, qty=0.2, lane="llm_evolving"):
    return LlmPaperFill(
        fill_id=f"fill-{action}", operation_id=f"op-{action}", decision_id="dec-1",
        position_id="pos-1", lane=lane, symbol="BTC/USDT",
        action=action, reason=action, side="long", qty=qty, price=price,
        fee_usd=0, realized_pnl_usd=-600 if action == "stop" else 0,
        balance_after_usd=9_400 if action == "stop" else 10_000,
        candle_ts=ts, created_at=NOW,
    )


def _snapshot():
    return MarketSnapshotBuilder(clock=lambda: NOW).build(
        cutoff=NOW, symbol="BTC/USDT",
        candles={"15m": [
            {"ts": OPEN_TS, "open": 100_000, "high": 101_000, "low": 99_000, "close": 100_000, "volume": 1},
            {"ts": CLOSE_TS, "open": 100_000, "high": 100_500, "low": 96_000, "close": 97_000, "volume": 1},
        ]},
        indicators={"15m": {"atr_14": 2_500}}, derivatives={"funding_rate": 0.0001},
        macro={}, onchain={}, evidence=[], paper_account={"positions": []},
    )


class PostMortem:
    def review(self, **kwargs):
        return SimpleNamespace(
            primary_error="poor_timing",
            lesson_adjustment_fr="Attendre une clôture de confirmation.",
            lesson_evidence_strength=0.8,
        )


def test_closed_position_creates_outcome_postmortem_and_candidate_lesson(tmp_path):
    fills = JsonlJournal(tmp_path / "fills.jsonl")
    fills.append(_fill("open", OPEN_TS, 100_000).to_mapping())
    close = _fill("stop", CLOSE_TS, 97_000)
    fills.append(close.to_mapping())
    decisions = JsonlJournal(tmp_path / "decisions.jsonl")
    decisions.append({
        "kind": "proposed_decision", "status": "valid", "decision_id": "dec-1",
        "brief_id": "brief-1", "paper_effective_leverage": 20,
        "decision": {
            "decision_id": "dec-1", "action": "open_long", "confidence": 0.7,
            "symbol": "BTC/USDT", "equity_fraction": 0.25,
            "stop_loss": 97_000, "take_profits": [{"price": 105_000, "fraction": 1}],
        },
    })
    decisions.append({
        "kind": "paper_validation", "decision_id": "dec-1",
        "validation": {"estimated_liquidation_price": 95_500},
    })
    briefs = JsonlJournal(tmp_path / "briefs.jsonl")
    briefs.append({
        "kind": "market_brief", "status": "valid",
        "brief": {"brief_id": "brief-1", "regime": "volatile_range", "narrative_vs_price": "absorption"},
    })
    outcomes = JsonlJournal(tmp_path / "outcomes.jsonl")
    lessons = LessonBook(JsonlJournal(tmp_path / "lessons.jsonl"), clock=lambda: NOW)
    processor = LearningProcessor(
        fills=fills, decisions=decisions, briefs=briefs, outcomes=outcomes,
        evaluator=OutcomeEvaluator(fee_rate=0), postmortem=PostMortem(),
        lessons=lessons, decision_timeframe="15m", clock=lambda: NOW,
    )

    result = processor.process(fill=close, snapshot=_snapshot())

    assert result.status == "learned"
    assert outcomes.read()[0]["outcome"]["exit_reason"] == "stop"
    assert outcomes.read()[0]["outcome"]["account_return"] == -0.06
    assert lessons.journal.read()[0]["lesson"]["state"] == "candidate"
    assert processor.process(fill=close, snapshot=_snapshot()).status == "already_learned"


def test_reference_lane_is_evaluated_but_never_trains_evolving_lessons(tmp_path):
    fills = JsonlJournal(tmp_path / "fills.jsonl")
    opening = _fill("open", OPEN_TS, 100_000, lane="llm_reference")
    close = _fill("stop", CLOSE_TS, 97_000, lane="llm_reference")
    fills.append(opening.to_mapping())
    fills.append(close.to_mapping())
    decisions = JsonlJournal(tmp_path / "decisions.jsonl")
    decisions.append({
        "kind": "proposed_decision", "brief_id": "brief-1",
        "decision": {"decision_id": "dec-1", "action": "open_long",
                     "symbol": "BTC/USDT", "equity_fraction": 0.25,
                     "confidence": 0.7},
    })
    briefs = JsonlJournal(tmp_path / "briefs.jsonl")
    briefs.append({"brief": {"brief_id": "brief-1", "regime": "range",
                              "narrative_vs_price": "neutral"}})
    outcomes = JsonlJournal(tmp_path / "outcomes.jsonl")
    lessons = LessonBook(JsonlJournal(tmp_path / "lessons.jsonl"), clock=lambda: NOW)
    processor = LearningProcessor(
        fills=fills, decisions=decisions, briefs=briefs, outcomes=outcomes,
        evaluator=OutcomeEvaluator(fee_rate=0), postmortem=PostMortem(),
        lessons=lessons, decision_timeframe="15m", clock=lambda: NOW,
    )

    result = processor.process(fill=close, snapshot=_snapshot())

    assert result.status == "evaluated_reference"
    assert len(outcomes.read()) == 1
    assert lessons.journal.read() == []


def test_partial_legacy_outcome_without_fill_ledger_fails_closed(tmp_path):
    outcomes = JsonlJournal(tmp_path / "outcomes.jsonl")
    outcomes.append({"kind": "decision_outcome", "outcome": {"decision_id": "dec-1"}})
    processor = LearningProcessor(
        fills=JsonlJournal(tmp_path / "fills.jsonl"),
        decisions=JsonlJournal(tmp_path / "decisions.jsonl"),
        briefs=JsonlJournal(tmp_path / "briefs.jsonl"), outcomes=outcomes,
        evaluator=OutcomeEvaluator(), postmortem=PostMortem(),
        lessons=LessonBook(JsonlJournal(tmp_path / "lessons.jsonl")),
        decision_timeframe="15m", clock=lambda: NOW,
    )

    assert processor.process(fill=_fill("stop", CLOSE_TS, 97_000), snapshot=_snapshot()).status == "missing_entry"
