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
        existing_outcome = None
        for record in self.outcomes.read():
            outcome = record.get("outcome")
            if isinstance(outcome, Mapping) and outcome.get("decision_id") == original_decision_id:
                existing_outcome = outcome
        existing_lesson = self.lessons.for_decision(original_decision_id)
        if existing_outcome is not None and existing_lesson is not None:
            return LearningResult(
                original_decision_id,
                "already_learned",
                str(existing_outcome.get("outcome_id")),
                existing_lesson.lesson_id,
            )

        decision_record = self._decision_record(original_decision_id)
        if decision_record is None:
            return LearningResult(original_decision_id, "missing_decision")
        decision = decision_record["decision"]
        entry_price = float(opening["price"])
        candles = [
            row for row in snapshot.candles.get(self.decision_timeframe, [])
            if int(opening["candle_ts"]) < int(row["ts"]) <= fill.candle_ts
        ]
        if existing_outcome is None and not candles:
            return LearningResult(original_decision_id, "missing_candles")
        if existing_outcome is None:
            outcome = self.evaluator.evaluate_fills(
                decision_id=original_decision_id, lane=fill.lane, symbol=fill.symbol,
                side=fill.side,
                equity_fraction=float(decision.get("equity_fraction", 1)),
                confidence=float(decision.get("confidence", 0.5)),
                fills=related, candles=candles,
                evaluated_at=self._clock().astimezone(UTC),
            )
            outcome_mapping = outcome.to_mapping()
            self.outcomes.append({
                "schema_version": 1, "kind": "decision_outcome",
                "recorded_at": self._clock().astimezone(UTC).isoformat(),
                "outcome": outcome_mapping,
            })
        else:
            outcome_mapping = existing_outcome
        existing_postmortem = getattr(self.postmortem, "existing", lambda **kwargs: None)(
            decision_id=original_decision_id,
            outcome_id=str(outcome_mapping["outcome_id"]),
        )
        postmortem = existing_postmortem or self.postmortem.review(
            decision=decision, outcome=outcome_mapping
        )
        if fill.lane != "llm_evolving":
            return LearningResult(
                original_decision_id,
                "evaluated_reference",
                str(outcome_mapping["outcome_id"]),
            )
        brief = self._brief(decision_record.get("brief_id"))
        case = self._case(
            snapshot,
            decision,
            brief,
            fill.side,
            entry_price,
            self.decision_timeframe,
        )
        lesson = self.lessons.record(LessonCandidate(
            error_category=postmortem.primary_error, conditions=case,
            adjustment=postmortem.lesson_adjustment_fr,
            decision_id=original_decision_id,
            evidence_strength=postmortem.lesson_evidence_strength,
            created_at=self._clock().astimezone(UTC),
            expires_at=self._clock().astimezone(UTC) + timedelta(days=90),
        ))
        return LearningResult(
            original_decision_id,
            "learned",
            str(outcome_mapping["outcome_id"]),
            lesson.lesson_id,
        )

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
    def _case(snapshot, decision, brief, side, entry_price, decision_timeframe):
        fraction = float(decision.get("equity_fraction", 0) or 0)
        exposure = "high" if fraction >= 0.5 else "medium" if fraction >= 0.2 else "low"
        return MarketCase(
            symbol=str(decision.get("symbol") or snapshot.symbol),
            regime=str(brief.get("regime", "unknown")),
            volatility_bucket="unknown", side=side,
            action=str(decision.get("action", "unknown")), funding_sign="unknown",
            oi_change_bucket="unknown",
            narrative_class=str(brief.get("narrative_vs_price", "unknown")),
            exposure_bucket=exposure,
        )
