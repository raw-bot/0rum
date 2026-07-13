from datetime import UTC, datetime

import pytest

from orum.llm.journal import JsonlJournal
from orum.llm.openrouter import CompletionResult
from orum.llm.postmortem import PostMortemError, PostMortemService


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class Client:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, **kwargs):
        return CompletionResult(
            payload=self.payload, model="deepseek/deepseek-v4-pro",
            latency_ms=50, usage={"total_tokens": 200}, request_id="req-1",
        )


def _payload(**overrides):
    value = {
        "postmortem_id": "pm-1", "decision_id": "dec-1", "outcome_id": "out-1",
        "process_quality": "bad_process", "primary_error": "poor_timing",
        "secondary_errors": ["leverage_size_mismatch"],
        "what_worked_fr": "L'invalidation était explicite.",
        "what_change_fr": "Attendre une clôture de confirmation.",
        "lesson_adjustment_fr": "Attendre une clôture au-dessus de la résistance.",
        "lesson_evidence_strength": 0.75,
        "memo_fr": "Le sens était plausible mais l'entrée était prématurée.",
    }
    value.update(overrides)
    return value


def test_postmortem_is_schema_validated_and_journaled(tmp_path):
    journal = JsonlJournal(tmp_path / "postmortems.jsonl")
    service = PostMortemService(
        client=Client(_payload()), journal=journal, clock=lambda: NOW
    )

    result = service.review(
        decision={"decision_id": "dec-1", "memo_fr": "Long"},
        outcome={"outcome_id": "out-1", "decision_id": "dec-1", "net_return_on_margin": -0.5},
    )

    assert result.primary_error == "poor_timing"
    assert result.lesson_adjustment_fr.startswith("Attendre")
    assert journal.read()[0]["status"] == "valid"


def test_postmortem_rejects_invented_provenance_and_journals_error(tmp_path):
    journal = JsonlJournal(tmp_path / "postmortems.jsonl")
    service = PostMortemService(
        client=Client(_payload(decision_id="invented")), journal=journal, clock=lambda: NOW
    )

    with pytest.raises(PostMortemError, match="decision_id mismatch"):
        service.review(
            decision={"decision_id": "dec-1"},
            outcome={"outcome_id": "out-1", "decision_id": "dec-1"},
        )

    assert journal.read()[0]["status"] == "model_error"
