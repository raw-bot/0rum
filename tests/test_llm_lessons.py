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
        error_category="poor_timing",
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
