"""Event-sourced, falsifiable lessons and deterministic similar-case retrieval."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from orum.llm.journal import JsonlJournal


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MarketCase:
    symbol: str
    regime: str
    volatility_bucket: str
    side: str
    action: str
    funding_sign: str
    oi_change_bucket: str
    narrative_class: str
    exposure_bucket: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "MarketCase":
        fields = set(cls.__dataclass_fields__)
        if set(value) != fields:
            raise ValueError("market case fields are incomplete or unknown")
        if not all(isinstance(value[name], str) and value[name].strip() for name in fields):
            raise ValueError("market case values must be non-empty text")
        return cls(**{name: str(value[name]).strip() for name in fields})

    def to_mapping(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class LessonCandidate:
    error_category: str
    conditions: MarketCase
    adjustment: str
    decision_id: str
    evidence_strength: float
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Lesson:
    lesson_id: str
    state: str
    error_category: str
    conditions: MarketCase
    adjustment: str
    supporting_decision_ids: tuple[str, ...]
    counterexample_decision_ids: tuple[str, ...]
    evidence_strength: float
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    version: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "Lesson":
        def stamp(name: str) -> datetime:
            result = datetime.fromisoformat(str(value[name]))
            return result.astimezone(UTC)
        return cls(
            lesson_id=str(value["lesson_id"]), state=str(value["state"]),
            error_category=str(value["error_category"]),
            conditions=MarketCase.from_mapping(value["conditions"]),
            adjustment=str(value["adjustment"]),
            supporting_decision_ids=tuple(value["supporting_decision_ids"]),
            counterexample_decision_ids=tuple(value.get("counterexample_decision_ids", [])),
            evidence_strength=float(value["evidence_strength"]),
            created_at=stamp("created_at"), updated_at=stamp("updated_at"),
            expires_at=stamp("expires_at"), version=int(value["version"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "lesson_id": self.lesson_id, "state": self.state,
            "error_category": self.error_category,
            "conditions": self.conditions.to_mapping(), "adjustment": self.adjustment,
            "supporting_decision_ids": list(self.supporting_decision_ids),
            "counterexample_decision_ids": list(self.counterexample_decision_ids),
            "evidence_strength": self.evidence_strength,
            "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(), "version": self.version,
        }


class LessonBook:
    def __init__(self, journal: JsonlJournal, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self.journal = journal
        self._clock = clock

    def record(self, candidate: LessonCandidate) -> Lesson:
        now = self._clock().astimezone(UTC)
        signature = "|".join((
            candidate.error_category, candidate.adjustment,
            *candidate.conditions.to_mapping().values(),
        ))
        lesson_id = f"lesson-{hashlib.sha256(signature.encode()).hexdigest()[:24]}"
        prior = self._latest().get(lesson_id)
        supports = tuple(dict.fromkeys(
            (() if prior is None else prior.supporting_decision_ids) + (candidate.decision_id,)
        ))
        lesson = Lesson(
            lesson_id=lesson_id,
            state="active" if len(supports) >= 2 else "candidate",
            error_category=candidate.error_category, conditions=candidate.conditions,
            adjustment=candidate.adjustment, supporting_decision_ids=supports,
            counterexample_decision_ids=() if prior is None else prior.counterexample_decision_ids,
            evidence_strength=max(candidate.evidence_strength, 0 if prior is None else prior.evidence_strength),
            created_at=candidate.created_at if prior is None else prior.created_at,
            updated_at=now, expires_at=candidate.expires_at,
            version=1 if prior is None else prior.version + 1,
        )
        self.journal.append({
            "schema_version": 1, "kind": "lesson_event", "recorded_at": now.isoformat(),
            "lesson": lesson.to_mapping(),
        })
        return lesson

    def retrieve(self, case: MarketCase, *, limit: int) -> tuple[Lesson, ...]:
        if limit <= 0:
            return ()
        now = self._clock().astimezone(UTC)
        candidates = [
            lesson for lesson in self._latest().values()
            if lesson.state == "active" and lesson.expires_at > now
        ]
        fields = tuple(case.to_mapping())
        candidates.sort(key=lambda lesson: (
            -sum(getattr(lesson.conditions, name) == getattr(case, name) for name in fields),
            -lesson.evidence_strength, -len(lesson.supporting_decision_ids),
            -lesson.updated_at.timestamp(), lesson.lesson_id,
        ))
        return tuple(candidates[:limit])

    def _latest(self) -> dict[str, Lesson]:
        latest: dict[str, Lesson] = {}
        for record in self.journal.read():
            raw = record.get("lesson")
            if isinstance(raw, Mapping):
                lesson = Lesson.from_mapping(raw)
                latest[lesson.lesson_id] = lesson
        return latest
