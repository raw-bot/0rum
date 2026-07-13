"""Connect final paper fills to deterministic outcomes, post-mortems and lessons."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from orum.llm.contracts import MarketSnapshot
from orum.llm.journal import JsonlJournal
from orum.llm.lessons import LessonBook, LessonCandidate, MarketCase
from orum.llm.outcomes import OutcomeEvaluator
from orum.llm.paper_contracts import LlmPaperFill


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LearningResult:
    decision_id: str
    status: str
    outcome_id: str | None = None
    lesson_id: str | None = None


class LearningProcessor:
    FINAL_ACTIONS = frozenset({"close", "stop", "liquidation", "time_exit", "take_profit"})

    def __init__(
        self, *, fills: JsonlJournal, decisions: JsonlJournal, briefs: JsonlJournal,
        outcomes: JsonlJournal, evaluator: OutcomeEvaluator, postmortem,
        lessons: LessonBook, decision_timeframe: str,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.fills, self.decisions, self.briefs = fills, decisions, briefs
        self.outcomes, self.evaluator, self.postmortem = outcomes, evaluator, postmortem
        self.lessons, self.decision_timeframe, self._clock = lessons, decision_timeframe, clock

    def process(self, *, fill: LlmPaperFill, snapshot: MarketSnapshot) -> LearningResult:
        if fill.action not in self.FINAL_ACTIONS:
            return LearningResult(fill.decision_id, "not_final")
        for record in self.outcomes.read():
            outcome = record.get("outcome")
            if isinstance(outcome, Mapping) and outcome.get("decision_id") == fill.decision_id:
                return LearningResult(fill.decision_id, "already_learned", outcome.get("outcome_id"))
        fill_rows = self.fills.read()
        related = [row for row in fill_rows if row.get("position_id") == fill.position_id]
        entries = [row for row in related if row.get("action") in {"open", "add"}]
        exits = [row for row in related if row.get("action") in self.FINAL_ACTIONS or row.get("action") == "reduce"]
        if not entries:
            return LearningResult(fill.decision_id, "missing_entry")
        if sum(float(row.get("qty", 0)) for row in exits) + 1e-12 < sum(
            float(row.get("qty", 0)) for row in entries
        ):
            return LearningResult(fill.decision_id, "position_still_open")
        opening = entries[0]
        original_decision_id = str(opening["decision_id"])
        for record in self.outcomes.read():
            outcome = record.get("outcome")
            if isinstance(outcome, Mapping) and outcome.get("decision_id") == original_decision_id:
                return LearningResult(original_decision_id, "already_learned", outcome.get("outcome_id"))

        decision_record = self._decision_record(original_decision_id)
        if decision_record is None:
            return LearningResult(original_decision_id, "missing_decision")
        decision = decision_record["decision"]
        validation = self._validation_record(original_decision_id)
        leverage = float(decision_record.get("paper_effective_leverage") or decision.get("requested_leverage") or 1)
        liquidation = (
            None if validation is None else validation.get("estimated_liquidation_price")
        )
        entry_price = float(opening["price"])
        if liquidation is None:
            liquidation = entry_price * (1 - 1 / leverage + 0.005) if fill.side == "long" else entry_price * (1 + 1 / leverage - 0.005)
        candles = [
            row for row in snapshot.candles.get(self.decision_timeframe, [])
            if int(opening["candle_ts"]) <= int(row["ts"]) <= fill.candle_ts
        ]
        if not candles:
            return LearningResult(original_decision_id, "missing_candles")
        targets = decision.get("take_profits") or []
        outcome = self.evaluator.evaluate(
            decision_id=original_decision_id, lane=fill.lane, symbol=fill.symbol,
            side=fill.side, entry_price=entry_price, leverage=leverage,
            equity_fraction=float(decision.get("equity_fraction", 1)),
            confidence=float(decision.get("confidence", 0.5)),
            stop_loss=decision.get("stop_loss"),
            take_profit=None if not targets else float(targets[0]["price"]),
            liquidation_price=float(liquidation), candles=candles,
            evaluated_at=self._clock().astimezone(UTC),
        )
        self.outcomes.append({
            "schema_version": 1, "kind": "decision_outcome",
            "recorded_at": self._clock().astimezone(UTC).isoformat(),
            "outcome": outcome.to_mapping(),
        })
        postmortem = self.postmortem.review(
            decision=decision, outcome=outcome.to_mapping()
        )
        brief = self._brief(decision_record.get("brief_id"))
        case = self._case(snapshot, decision, brief, fill.side, entry_price)
        lesson = self.lessons.record(LessonCandidate(
            error_category=postmortem.primary_error, conditions=case,
            adjustment=postmortem.lesson_adjustment_fr,
            decision_id=original_decision_id,
            evidence_strength=postmortem.lesson_evidence_strength,
            created_at=self._clock().astimezone(UTC),
            expires_at=self._clock().astimezone(UTC) + timedelta(days=90),
        ))
        return LearningResult(original_decision_id, "learned", outcome.outcome_id, lesson.lesson_id)

    def _decision_record(self, decision_id: str):
        for record in reversed(self.decisions.read()):
            decision = record.get("decision")
            if record.get("kind") == "proposed_decision" and isinstance(decision, Mapping) and decision.get("decision_id") == decision_id:
                return record
        return None

    def _validation_record(self, decision_id: str):
        for record in reversed(self.decisions.read()):
            if record.get("kind") == "paper_validation" and record.get("decision_id") == decision_id:
                value = record.get("validation")
                return value if isinstance(value, Mapping) else None
        return None

    def _brief(self, brief_id):
        for record in reversed(self.briefs.read()):
            brief = record.get("brief")
            if isinstance(brief, Mapping) and brief.get("brief_id") == brief_id:
                return brief
        return {}

    @staticmethod
    def _case(snapshot, decision, brief, side, entry_price):
        indicators = snapshot.indicators.get("15m", {})
        atr = indicators.get("atr_14") if isinstance(indicators, Mapping) else None
        volatility = "unknown" if atr is None else ("high" if float(atr) / entry_price >= 0.02 else "normal")
        derivatives = snapshot.derivatives or {}
        funding = derivatives.get("funding_rate")
        funding_sign = "unknown" if funding is None else ("positive" if float(funding) >= 0 else "negative")
        return MarketCase(
            symbol=snapshot.symbol, regime=str(brief.get("regime", "unknown")),
            volatility_bucket=volatility, side=side,
            action=str(decision.get("action", "unknown")), funding_sign=funding_sign,
            oi_change_bucket="unknown",
            narrative_class=str(brief.get("narrative_vs_price", "unknown")),
            exposure_bucket="low",
        )
