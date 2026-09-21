from datetime import UTC, datetime, timedelta

from orum.llm.journal import JsonlJournal
from orum.llm.lessons import LessonBook, LessonCandidate, MarketCase


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
CASE = MarketCase(
    symbol="BTC/USDT", regime="volatile_range", volatility_bucket="high",
    side="long", action="open_long", funding_sign="positive",
    oi_change_bucket="rising", narrative_class="absorption",
    exposure_bucket="low",
)


def _candidate(decision_id):
    return LessonCandidate(
        error_category="poor_timing", adjustment_key="confirm_entry",
        conditions=CASE,
        adjustment="Attendre une clôture au-dessus de la résistance avant l'entrée.",
        decision_id=decision_id,
        evidence_strength=0.8,
        created_at=NOW,
        expires_at=NOW + timedelta(days=90),
    )


def test_single_case_stays_candidate_and_second_support_activates(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / "lessons.jsonl"), clock=lambda: NOW)

    first = book.record(_candidate("dec-1"))
    second = book.record(_candidate("dec-2"))

    assert first.state == "candidate"
    assert second.state == "active"
    assert second.supporting_decision_ids == ("dec-1", "dec-2")


def test_retrieval_is_deterministic_active_only_and_bounded(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / "lessons.jsonl"), clock=lambda: NOW)
    book.record(_candidate("dec-1"))
    active = book.record(_candidate("dec-2"))
    different = LessonCandidate(
        error_category="wrong_direction",
        conditions=MarketCase(**{**CASE.to_mapping(), "side": "short"}),
        adjustment="Ne pas vendre une absorption haussière.", decision_id="dec-3",
        evidence_strength=0.9, created_at=NOW, expires_at=NOW + timedelta(days=90),
    )
    book.record(different)

    retrieved = book.retrieve(CASE, limit=1)

    assert [lesson.lesson_id for lesson in retrieved] == [active.lesson_id]


def test_expired_lesson_is_not_retrieved(tmp_path):
    clock = [NOW]
    book = LessonBook(JsonlJournal(tmp_path / "lessons.jsonl"), clock=lambda: clock[0])
    book.record(_candidate("dec-1"))
    book.record(_candidate("dec-2"))
    clock[0] = NOW + timedelta(days=91)

    assert book.retrieve(CASE, limit=5) == ()


def test_unrelated_active_lesson_is_not_retrieved(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / "lessons.jsonl"), clock=lambda: NOW)
    book.record(_candidate("dec-1"))
    book.record(_candidate("dec-2"))
    unrelated = MarketCase(**{name: f"other-{name}" for name in CASE.to_mapping()})
    assert book.retrieve(unrelated, limit=5) == ()


def test_counterexamples_reject_active_lesson(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / "lessons.jsonl"), clock=lambda: NOW)
    book.record(_candidate("dec-1"))
    active = book.record(_candidate("dec-2"))
    book.record_counterexample(active.lesson_id, "bad-1")
    rejected = book.record_counterexample(active.lesson_id, "bad-2")
    assert rejected.state == "rejected"
    assert book.retrieve(CASE, limit=5) == ()

from dataclasses import replace
import hashlib
import json
import pytest


def test_v2_merges_paraphrases_and_regime_aliases_not_other_markets(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / 'lessons.jsonl'), clock=lambda: NOW)
    first = replace(_candidate('one'), adjustment_key='confirm_entry', conditions=replace(CASE, regime='trend_down'))
    a = book.record(first)
    b = book.record(replace(first, decision_id='two', adjustment='Autre formulation', conditions=replace(first.conditions, regime='downtrend', narrative_class='autre récit')))
    assert a.lesson_id == b.lesson_id and b.state == 'active'
    assert book.retrieve(replace(CASE, regime='trending_down'), limit=5) == (b,)
    for case in (replace(CASE, regime='range'), replace(CASE, regime='downtrend', symbol='ETH/USDT'), replace(CASE, regime='downtrend', side='short')):
        assert book.retrieve(case, limit=5) == ()
    before = book.journal.path.read_bytes()
    assert book.record(replace(first, expires_at=NOW+timedelta(days=200))) == b
    assert book.journal.path.read_bytes() == before


def test_v2_expired_support_cannot_be_refreshed_and_rejection_is_final(tmp_path):
    clock = [NOW]
    book = LessonBook(JsonlJournal(tmp_path / 'lessons.jsonl'), clock=lambda: clock[0])
    first = replace(_candidate('one'), adjustment_key='confirm_entry', expires_at=NOW+timedelta(days=1))
    book.record(first)
    active = book.record(replace(first, decision_id='two', expires_at=NOW+timedelta(days=90)))
    clock[0] += timedelta(days=2)
    assert book.retrieve(CASE, limit=5) == ()
    with pytest.raises(ValueError, match='support and contradict'):
        book.record_counterexample(active.lesson_id, 'one')
    book.record_counterexample(active.lesson_id, 'bad1')
    rejected = book.record_counterexample(active.lesson_id, 'bad2')
    terminal = book.record(replace(first, decision_id='three'))
    assert terminal.adjustment_key is None and terminal.state == 'candidate'
    assert book.for_decision('three') == terminal
    assert book._latest()[rejected.lesson_id] == rejected
    assert rejected.state == 'rejected'


def test_incompatible_codes_and_opposing_hypotheses(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / 'lessons.jsonl'), clock=lambda: NOW)
    with pytest.raises(ValueError, match='incompatible'):
        book.record(replace(_candidate('one'), adjustment_key='stop_beyond_noise'))
    for key, error in [('target_within_horizon','target_too_ambitious'),('let_winners_run','target_too_conservative')]:
        for number in range(2):
            book.record(replace(_candidate(key+str(number)), adjustment_key=key, error_category=error))
    assert book.retrieve(CASE, limit=5) == ()


def test_migration_atomic_idempotent_guarded_and_preserves_evidence(tmp_path, monkeypatch):
    journal = JsonlJournal(tmp_path / 'lessons.jsonl')
    book = LessonBook(journal, clock=lambda: NOW)
    sources = [book.record(replace(_candidate(str(i)), adjustment='Texte '+str(i), adjustment_key=None)) for i in range(2)]
    plan = {'migration_id':'test-v2','bindings':[{'lesson_id':x.lesson_id,'source_sha256':hashlib.sha256(json.dumps(x.to_mapping(),sort_keys=True).encode()).hexdigest(),'adjustment_key':'confirm_entry'} for x in sources]}
    before = journal.path.read_bytes()
    dry = book.migrate(plan)
    assert journal.path.read_bytes() == before
    import orum.llm.lessons as module
    original = module.atomic_write_text
    def fail(*args):
        raise OSError('simulated publication failure')
    monkeypatch.setattr(module, 'atomic_write_text', fail)
    with pytest.raises(OSError):
        book.migrate(plan, apply=True)
    assert journal.path.read_bytes() == before
    monkeypatch.setattr(module, 'atomic_write_text', original)
    applied = book.migrate(plan, apply=True)
    assert applied == dry and journal.path.read_bytes().startswith(before)
    assert len(journal.read()) == 3
    assert book.migrate(plan, apply=True) == applied
    assert len(journal.read()) == 3
    assert len(book.retrieve(CASE, limit=5)) == 1
    lesson = book.for_decision('0')
    assert lesson.state == 'active' and lesson.created_at == NOW and lesson.expires_at == sources[0].expires_at
    with pytest.raises(ValueError, match='another plan'):
        book.migrate({**plan, 'note':'changed'})
    with pytest.raises(ValueError, match='duplicate'):
        book.migrate({**plan, 'bindings':plan['bindings']*2})


def test_unclassified_and_unknown_regime_are_not_injectable(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / 'lessons.jsonl'), clock=lambda: NOW)
    for i in range(2):
        candidate = book.record(replace(_candidate(str(i)), adjustment_key=None))
    assert candidate.state == 'candidate' and book.retrieve(CASE, limit=5) == ()
    unknown = replace(CASE, regime='unknown')
    for i in range(2):
        book.record(replace(_candidate('unknown'+str(i)), conditions=unknown))
    assert book.retrieve(unknown, limit=5) == ()


def test_migration_into_rejected_rule_cannot_create_self_alias(tmp_path):
    book = LessonBook(JsonlJournal(tmp_path / 'lessons.jsonl'), clock=lambda: NOW)
    book.record(_candidate('a'))
    active = book.record(_candidate('b'))
    book.record_counterexample(active.lesson_id, 'c')
    book.record_counterexample(active.lesson_id, 'd')
    source = book.record(replace(_candidate('legacy'), adjustment_key=None))
    plan = {'migration_id':'reject','bindings':[{'lesson_id':source.lesson_id,'adjustment_key':'confirm_entry','source_sha256':hashlib.sha256(json.dumps(source.to_mapping(),sort_keys=True).encode()).hexdigest()}]}
    before = book.journal.path.read_bytes()
    with pytest.raises(ValueError, match='revive'):
        book.migrate(plan, apply=True)
    assert book.journal.path.read_bytes() == before
    assert book.for_decision('legacy') == source
