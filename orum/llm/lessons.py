"""Event-sourced, falsifiable lessons and deterministic similar-case retrieval."""

from __future__ import annotations

import hashlib
import json
import fcntl
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace, field
from datetime import UTC, datetime

from orum.llm.journal import JsonlJournal
from orum.fsio import atomic_write_text
from orum.llm.lesson_rules import RULES, OPPOSITES, regime_key, scope, validate_rule


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
    adjustment_key: str | None = None


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
    adjustment_key: str | None = None
    superseded_by: str | None = None
    support_expiries: dict[str, str] = field(default_factory=dict)

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
            adjustment_key=value.get("adjustment_key"), superseded_by=value.get("superseded_by"),
            support_expiries=dict(value.get("support_expiries", {})),
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
            "adjustment_key": self.adjustment_key, "superseded_by": self.superseded_by,
            "support_expiries": self.support_expiries,
        }


class LessonBook:
    def __init__(self, journal: JsonlJournal, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self.journal = journal
        self._clock = clock

    @contextmanager
    def mutation_lock(self):
        path = self.journal.path.with_name(self.journal.path.name + ".learning.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def record(self, candidate: LessonCandidate) -> Lesson:
        with self.mutation_lock():
            return self._record_candidate(candidate)

    def _record_candidate(self, candidate: LessonCandidate) -> Lesson:
        now = self._clock().astimezone(UTC)
        validate_rule(candidate.adjustment_key, candidate.error_category)
        signature = ("v2|" + json.dumps([candidate.error_category, candidate.adjustment_key, *scope(candidate.conditions)])
                     if candidate.adjustment_key else "|".join((candidate.error_category, candidate.adjustment,
                                                              *candidate.conditions.to_mapping().values())))
        lesson_id = f"lesson-{hashlib.sha256(signature.encode()).hexdigest()[:24]}"
        prior = self._latest().get(lesson_id)
        if candidate.adjustment_key is not None and prior is not None and (candidate.decision_id in prior.counterexample_decision_ids or prior.state == "rejected"):
            # Keep a terminal, unclassified observation without reviving the hypothesis.
            return self._record_candidate(replace(candidate, adjustment_key=None))
        if prior is not None and (candidate.decision_id in prior.supporting_decision_ids
                                  or candidate.decision_id in prior.counterexample_decision_ids
                                  or prior.state in {"rejected", "superseded"}):
            return prior
        supports = tuple(dict.fromkeys(
            (() if prior is None else prior.supporting_decision_ids) + (candidate.decision_id,)
        ))
        conflicts = [
            item for item in self._latest().values()
            if item.lesson_id != lesson_id
            and item.error_category == candidate.error_category
            and item.conditions == candidate.conditions
            and item.adjustment != candidate.adjustment
            and candidate.adjustment_key is None
            and item.state in {"candidate", "active"}
        ]
        expiries = dict(prior.support_expiries) if prior else {}
        if prior:
            for decision_id in prior.supporting_decision_ids:
                expiries.setdefault(decision_id, prior.expires_at.isoformat())
        expiries[candidate.decision_id] = candidate.expires_at.isoformat()
        fresh_supports = sum(datetime.fromisoformat(value) > now for value in expiries.values())
        counterexamples = () if prior is None else prior.counterexample_decision_ids
        lesson = Lesson(
            lesson_id=lesson_id,
            state="active" if candidate.adjustment_key is not None and fresh_supports - len(counterexamples) >= 2 and not conflicts else "candidate",
            error_category=candidate.error_category,
            conditions=(replace(candidate.conditions, regime=regime_key(candidate.conditions.regime),
                                volatility_bucket="unknown", action="unknown", funding_sign="unknown",
                                oi_change_bucket="unknown", narrative_class="unknown", exposure_bucket="unknown")
                        if candidate.adjustment_key else candidate.conditions),
            adjustment=RULES.get(candidate.adjustment_key, candidate.adjustment), supporting_decision_ids=supports,
            counterexample_decision_ids=() if prior is None else prior.counterexample_decision_ids,
            evidence_strength=max(candidate.evidence_strength, 0 if prior is None else prior.evidence_strength),
            created_at=candidate.created_at if prior is None else min(prior.created_at, candidate.created_at),
            updated_at=now, expires_at=max(datetime.fromisoformat(value) for value in expiries.values()),
            version=1 if prior is None else prior.version + 1,
            adjustment_key=candidate.adjustment_key, support_expiries=expiries,
        )
        self.journal.append({
            "schema_version": 1, "kind": "lesson_event", "recorded_at": now.isoformat(),
            "lesson": lesson.to_mapping(), "observation": candidate.adjustment,
        })
        return lesson

    def record_counterexample(self, lesson_id: str, decision_id: str) -> Lesson:
        with self.mutation_lock():
            return self._counterexample(lesson_id, decision_id)

    def _counterexample(self, lesson_id: str, decision_id: str) -> Lesson:
        now = self._clock().astimezone(UTC)
        prior = self._latest().get(lesson_id)
        if prior is None:
            raise ValueError("unknown lesson_id")
        if prior.superseded_by:
            return self._counterexample(prior.superseded_by, decision_id)
        if decision_id in prior.supporting_decision_ids:
            raise ValueError("a decision cannot support and contradict the same lesson")
        if decision_id in prior.counterexample_decision_ids:
            return prior
        counterexamples = tuple(dict.fromkeys(
            prior.counterexample_decision_ids + (decision_id,)
        ))
        state = (
            "rejected"
            if len(counterexamples) >= len(prior.supporting_decision_ids)
            else "candidate" if len(prior.supporting_decision_ids) - len(counterexamples) < 2 else prior.state
        )
        lesson = replace(
            prior,
            state=state,
            counterexample_decision_ids=counterexamples,
            updated_at=now,
            version=prior.version + 1,
        )
        self.journal.append({
            "schema_version": 1, "kind": "lesson_counterexample",
            "recorded_at": now.isoformat(), "lesson": lesson.to_mapping(),
        })
        return lesson

    def retrieve(self, case: MarketCase, *, limit: int) -> tuple[Lesson, ...]:
        if limit <= 0:
            return ()
        now = self._clock().astimezone(UTC)
        latest = self._latest()
        fields = tuple(case.to_mapping())
        candidates = []
        for lesson in latest.values():
            if not self.is_eligible(lesson, now=now):
                continue
            if regime_key(case.regime) in {"unknown", "inconnu", "n/a"} or regime_key(lesson.conditions.regime) in {"unknown", "inconnu", "n/a"}:
                continue
            if lesson.conditions.symbol != case.symbol or regime_key(lesson.conditions.regime) != regime_key(case.regime):
                continue
            if case.side != "unknown" and lesson.conditions.side != case.side:
                continue
            candidates.append(lesson)
        candidates.sort(key=lambda lesson: (
            -sum(getattr(case, name) != "unknown" and getattr(lesson.conditions, name) == getattr(case, name) for name in fields),
            -lesson.evidence_strength, -len(lesson.supporting_decision_ids),
            -lesson.updated_at.timestamp(), lesson.lesson_id,
        ))
        return tuple(candidates[:limit])

    def is_eligible(self, lesson: Lesson, *, now: datetime | None = None) -> bool:
        now = now or self._clock().astimezone(UTC)
        def fresh(item):
            return (item.state == "active" and item.adjustment_key is not None and item.expires_at > now
                    and regime_key(item.conditions.regime) not in {"unknown", "inconnu", "n/a"}
                    and (not item.support_expiries or sum(datetime.fromisoformat(x) > now for x in item.support_expiries.values()) - len(item.counterexample_decision_ids) >= 2))
        return fresh(lesson) and not any(fresh(other) and scope(other.conditions) == scope(lesson.conditions)
                                         and frozenset({other.adjustment_key, lesson.adjustment_key}) in OPPOSITES
                                         for other in self._latest().values())

    def for_decision(self, decision_id: str) -> Lesson | None:
        for lesson in self._latest().values():
            if lesson.state != "superseded" and decision_id in lesson.supporting_decision_ids:
                return lesson
        return None

    def migrate(self, plan: dict, *, apply: bool = False) -> dict:
        """One append-only event, atomically published under the lesson writer lock."""
        with self.mutation_lock():
            records = self.journal.read()
            plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            if len({item["lesson_id"] for item in plan["bindings"]}) != len(plan["bindings"]):
                raise ValueError("duplicate migration source")
            for row in records:
                if row.get("migration_id") == plan["migration_id"]:
                    if row["plan_sha256"] != plan_hash:
                        raise ValueError("migration_id already used with another plan")
                    return row
            latest = self._latest()
            buffered = list(records)
            class Buffer:
                def read(self):
                    return list(buffered)
                def append(self, row):
                    buffered.append(row)
            target = LessonBook(Buffer(), clock=self._clock)
            changed = {}
            aliases = {}
            now = self._clock().astimezone(UTC)
            for binding in plan["bindings"]:
                source = latest[binding["lesson_id"]]
                fingerprint = hashlib.sha256(json.dumps(source.to_mapping(), sort_keys=True).encode()).hexdigest()
                if fingerprint != binding["source_sha256"]:
                    raise ValueError("lesson source changed since migration review")
                if source.state != "candidate" or source.counterexample_decision_ids or source.expires_at <= now:
                    raise ValueError("only unexpired, unopposed candidates may be migrated")
                for decision_id in source.supporting_decision_ids:
                    result = target._record_candidate(LessonCandidate(
                        error_category=source.error_category, conditions=source.conditions,
                        adjustment=source.adjustment, adjustment_key=binding["adjustment_key"],
                        decision_id=decision_id, evidence_strength=source.evidence_strength,
                        created_at=source.created_at, expires_at=source.expires_at,
                    ))
                if result.state == "rejected" or result.adjustment_key != binding["adjustment_key"]:
                    raise ValueError("migration cannot revive a rejected rule")
                changed[result.lesson_id] = result
                aliases[source.lesson_id] = result.lesson_id
            for source_id, target_id in aliases.items():
                source = latest[source_id]
                changed[source_id] = replace(source, state="superseded", superseded_by=target_id,
                                             updated_at=now, version=source.version + 1)
            event = {"schema_version": 2, "kind": "lesson_migration", "migration_id": plan["migration_id"],
                     "recorded_at": now.isoformat(), "aliases": aliases,
                     "plan_sha256": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
                     "lessons": [value.to_mapping() for value in changed.values()]}
            if apply:
                # Atomic replacement keeps the exact old byte prefix and adds a
                # single complete event: readers see all aliases or none of them.
                previous = self.journal.path.read_text() if self.journal.path.exists() else ""
                atomic_write_text(self.journal.path, previous + ("\n" if previous and not previous.endswith("\n") else "")
                                  + json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            return event

    def _latest(self) -> dict[str, Lesson]:
        latest: dict[str, Lesson] = {}
        for record in self.journal.read():
            values = record.get("lessons", []) + ([record["lesson"]] if isinstance(record.get("lesson"), Mapping) else [])
            for raw in values:
                lesson = Lesson.from_mapping(raw)
                latest[lesson.lesson_id] = lesson
        return latest
