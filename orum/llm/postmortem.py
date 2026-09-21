"""Bounded LLM post-mortems that cannot rewrite deterministic outcome metrics."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from orum.llm.journal import JsonlJournal
from orum.llm.lesson_rules import RULES, ALLOWED_ERRORS, validate_rule
from orum.llm.openrouter import CompletionResult, JsonCompletionClient


ERRORS = frozenset({
    "wrong_direction", "poor_timing", "stop_too_tight", "stop_too_loose",
    "target_too_ambitious", "target_too_conservative", "leverage_size_mismatch",
    "narrative_misread", "price_reaction_ignored", "portfolio_exposure_ignored",
    "regime_change", "stale_or_missing_data", "structured_text_inconsistency",
    "simulator_failure", "good_process_bad_luck", "bad_process_lucky_profit",
})


class PostMortemError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PostMortem:
    postmortem_id: str
    decision_id: str
    outcome_id: str
    process_quality: str
    primary_error: str
    secondary_errors: tuple[str, ...]
    what_worked_fr: str
    what_change_fr: str
    lesson_adjustment_fr: str
    lesson_evidence_strength: float
    memo_fr: str
    adjustment_key: str | None = None
    contradicted_lesson_ids: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PostMortem":
        fields = set(cls.__dataclass_fields__)
        optional = {"adjustment_key", "contradicted_lesson_ids"}
        if not fields - optional <= set(value) or set(value) - fields:
            raise PostMortemError("post-mortem fields are incomplete or unknown")
        texts = ("postmortem_id", "decision_id", "outcome_id", "what_worked_fr",
                 "what_change_fr", "lesson_adjustment_fr", "memo_fr")
        if any(not isinstance(value[name], str) or not value[name].strip() for name in texts):
            raise PostMortemError("post-mortem text fields must be non-empty")
        quality = str(value["process_quality"])
        if quality not in {"good_process", "mixed_process", "bad_process"}:
            raise PostMortemError("invalid process_quality")
        primary = str(value["primary_error"])
        secondary = value["secondary_errors"]
        if primary not in ERRORS or isinstance(secondary, (str, bytes)) or not isinstance(secondary, Sequence):
            raise PostMortemError("invalid error taxonomy")
        second = tuple(str(item) for item in secondary)
        if any(item not in ERRORS for item in second):
            raise PostMortemError("invalid secondary error taxonomy")
        strength = float(value["lesson_evidence_strength"])
        if not 0 <= strength <= 1:
            raise PostMortemError("lesson_evidence_strength must be between 0 and 1")
        key = value.get("adjustment_key")
        try:
            validate_rule(key, primary)
        except ValueError as exc:
            raise PostMortemError(str(exc)) from exc
        contradictions = value.get("contradicted_lesson_ids", [])
        if not isinstance(contradictions, (list, tuple)) or any(not isinstance(x, str) for x in contradictions):
            raise PostMortemError("invalid contradicted lessons")
        return cls(
            postmortem_id=str(value["postmortem_id"]), decision_id=str(value["decision_id"]),
            outcome_id=str(value["outcome_id"]), process_quality=quality,
            primary_error=primary, secondary_errors=second,
            what_worked_fr=str(value["what_worked_fr"]),
            what_change_fr=str(value["what_change_fr"]),
            lesson_adjustment_fr=str(value["lesson_adjustment_fr"]),
            lesson_evidence_strength=strength, memo_fr=str(value["memo_fr"]),
            adjustment_key=key, contradicted_lesson_ids=tuple(contradictions),
        )

    def to_mapping(self) -> dict[str, object]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["secondary_errors"] = list(self.secondary_errors)
        result["contradicted_lesson_ids"] = list(self.contradicted_lesson_ids)
        return result


POSTMORTEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": [name for name in PostMortem.__dataclass_fields__ if name not in {"adjustment_key", "contradicted_lesson_ids"}],
    "properties": {
        "postmortem_id": {"type": "string", "minLength": 1},
        "decision_id": {"type": "string", "minLength": 1},
        "outcome_id": {"type": "string", "minLength": 1},
        "process_quality": {"enum": ["good_process", "mixed_process", "bad_process"]},
        "primary_error": {"enum": sorted(ERRORS)},
        "secondary_errors": {"type": "array", "items": {"enum": sorted(ERRORS)}, "uniqueItems": True},
        "what_worked_fr": {"type": "string", "minLength": 1},
        "what_change_fr": {"type": "string", "minLength": 1},
        "lesson_adjustment_fr": {"type": "string", "minLength": 1},
        "lesson_evidence_strength": {"type": "number", "minimum": 0, "maximum": 1},
        "memo_fr": {"type": "string", "minLength": 1},
    },
}


class PostMortemService:
    def __init__(
        self, *, client: JsonCompletionClient, journal: JsonlJournal,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.client, self.journal, self._clock = client, journal, clock

    def review(self, *, decision: Mapping[str, object], outcome: Mapping[str, object]) -> PostMortem:
        completion: CompletionResult | None = None
        raw = None
        try:
            evolving = decision.get("lane") == "llm_evolving"
            schema = dict(POSTMORTEM_SCHEMA)
            if evolving:
                schema = {**schema, "required": [*schema["required"], "adjustment_key", "contradicted_lesson_ids"],
                          "properties": {**schema["properties"],
                                         "adjustment_key": {"enum": [None, *RULES]},
                                         "contradicted_lesson_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}}}
            completion = self.client.complete_json(
                system=(
                    "Tu réalises un post-mortem de trading paper. Les métriques fournies sont "
                    "immuables. Distingue processus et hasard, utilise uniquement la taxonomie, "
                    "et propose une leçon falsifiable en français."
                ) + ((" Choisis adjustment_key dans le catalogue, ou null si aucune hypothèse ne convient. "
                      "N'invente pas de soutien à une règle qui contredit ton analyse. Les catégories admises sont explicites. "
                      "Les leçons sont des hypothèses paper, pas des règles validées ni des veto. "
                      "contradicted_lesson_ids contient seulement des leçons fournies ET citées dont la recommandation "
                      "est contredite par le processus observé : explique pourquoi dans what_change_fr. Une perte seule "
                      "ou good_process_bad_luck n'est pas une réfutation. Catalogue : "
                      + json.dumps({key: {"rule": text, "errors": sorted(ALLOWED_ERRORS[key])} for key, text in RULES.items()}, ensure_ascii=False)) if evolving else ""),
                user=json.dumps({"decision": dict(decision), "outcome": dict(outcome)}, ensure_ascii=False),
                schema=schema,
                schema_name="llm_trade_postmortem",
            )
            raw = completion.payload
            result = PostMortem.from_mapping(raw)
            allowed = set(decision.get("lesson_ids", [])) & {
                item.get("lesson_id") for item in decision.get("provided_lessons", [])
            }
            if set(result.contradicted_lesson_ids) - allowed or (result.contradicted_lesson_ids and result.primary_error == "good_process_bad_luck"):
                raise PostMortemError("unsupported lesson contradiction")
            if result.adjustment_key and any(item.get("lesson_id") in result.contradicted_lesson_ids
                                             and item.get("adjustment_key") == result.adjustment_key
                                             for item in decision.get("provided_lessons", [])):
                raise PostMortemError("cannot support and contradict the same hypothesis")
            if result.decision_id != decision.get("decision_id"):
                raise PostMortemError("decision_id mismatch")
            if result.outcome_id != outcome.get("outcome_id"):
                raise PostMortemError("outcome_id mismatch")
        except Exception as exc:  # noqa: BLE001 - every model failure is visible
            error = exc if isinstance(exc, PostMortemError) else PostMortemError(str(exc))
            self.journal.append(self._record("model_error", decision, outcome, completion, raw, error))
            raise error from exc
        self.journal.append(self._record("valid", decision, outcome, completion, result.to_mapping(), None))
        return result

    def existing(self, *, decision_id: str, outcome_id: str) -> PostMortem | None:
        for record in reversed(self.journal.read()):
            payload = record.get("postmortem")
            if (
                record.get("status") == "valid"
                and record.get("decision_id") == decision_id
                and record.get("outcome_id") == outcome_id
                and isinstance(payload, Mapping)
            ):
                return PostMortem.from_mapping(payload)
        return None

    def _record(self, status, decision, outcome, completion, payload, error):
        return {
            "schema_version": 1, "kind": "llm_postmortem", "status": status,
            "recorded_at": self._clock().astimezone(UTC).isoformat(),
            "decision_id": decision.get("decision_id"), "outcome_id": outcome.get("outcome_id"),
            "model": None if completion is None else completion.model,
            "latency_ms": None if completion is None else completion.latency_ms,
            "usage": None if completion is None else completion.usage,
            "postmortem": payload if status == "valid" else None,
            "raw_payload": payload if status != "valid" else None,
            "error": None if error is None else str(error)[:1000],
        }
