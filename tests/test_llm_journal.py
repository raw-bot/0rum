import json
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context

import pytest

from orum.llm.journal import JournalError, JsonlJournal


def _append_many(path, start, count):
    journal = JsonlJournal(path)
    for number in range(start, start + count):
        journal.append({"sequence": number, "payload": "x" * 1000})


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


def test_journal_serializes_appends_across_processes(tmp_path):
    path = tmp_path / "multiprocess.jsonl"
    context = get_context("spawn")
    processes = [
        context.Process(target=_append_many, args=(path, index * 20, 20))
        for index in range(3)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    rows = JsonlJournal(path).read()
    assert len(rows) == 60
    assert {row["sequence"] for row in rows} == set(range(60))


def test_missing_journal_is_empty_and_limit_must_be_positive(tmp_path):
    journal = JsonlJournal(tmp_path / "lessons.jsonl")

    assert journal.read() == []
    with pytest.raises(JournalError, match="limit"):
        journal.read(limit=0)
