import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from orum.llm.journal import JournalError, JsonlJournal


def test_journal_appends_without_rewriting_prior_records(tmp_path):
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    journal.append({"decision_id": "one", "status": "valid"})
    first = (tmp_path / "decisions.jsonl").read_bytes()

    journal.append({"decision_id": "two", "status": "rejected"})

    assert (tmp_path / "decisions.jsonl").read_bytes().startswith(first)
    assert [row["decision_id"] for row in journal.read(limit=1)] == ["two"]


@pytest.mark.parametrize("record", [["not", "an", "object"], {"bad": float("nan")}])
def test_journal_rejects_non_object_and_non_json_values(tmp_path, record):
    journal = JsonlJournal(tmp_path / "decisions.jsonl")

    with pytest.raises(JournalError):
        journal.append(record)

    assert not (tmp_path / "decisions.jsonl").exists()


def test_journal_read_is_chronological_and_reports_corruption(tmp_path):
    path = tmp_path / "briefs.jsonl"
    path.write_text('{"brief_id":"one"}\n\nnot-json\n')
    journal = JsonlJournal(path)

    with pytest.raises(JournalError, match="line 3"):
        journal.read()


def test_journal_serializes_concurrent_appends_as_complete_lines(tmp_path):
    journal = JsonlJournal(tmp_path / "outcomes.jsonl")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda number: journal.append({"sequence": number}), range(40)))

    rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert len(rows) == 40
    assert {row["sequence"] for row in rows} == set(range(40))


def test_missing_journal_is_empty_and_limit_must_be_positive(tmp_path):
    journal = JsonlJournal(tmp_path / "lessons.jsonl")

    assert journal.read() == []
    with pytest.raises(JournalError, match="limit"):
        journal.read(limit=0)
