import json
from pathlib import Path

from scripts import replay_llm_lab


def test_builtin_replay_proves_aggressive_paper_and_learning_is_reproducible():
    fixture = replay_llm_lab.default_fixture()

    first = replay_llm_lab.run_replay(fixture)
    second = replay_llm_lab.run_replay(fixture)

    assert first == second
    assert first["replay_digest"] == second["replay_digest"]
    accepted = [row for row in first["decisions"] if row["status"] == "executed"]
    assert [(row["lane"], row["paper_effective_leverage"]) for row in accepted] == [
        ("llm_reference", 20),
        ("llm_evolving", 40),
    ]
    assert [row["fr_retail_eligible_leverage"] for row in accepted] == [2, 2]
    assert all(row["experimental_only"] for row in accepted)
    duplicate = first["decisions"][2]
    assert duplicate["status"] == "rejected"
    assert "decision_already_processed" in duplicate["reasons"]
    final_fills = [row for row in first["fills"] if row["action"] != "open"]
    assert [row["action"] for row in final_fills] == ["liquidation", "liquidation"]
    assert first["comparison"]["coverage_status"] == "common_window"
    assert first["lessons"][-1]["state"] == "active"
    assert first["retrieved_lessons"]["llm_reference"] == []
    assert first["retrieved_lessons"]["llm_evolving"] == [
        first["lessons"][-1]["lesson_id"]
    ]
    assert "api_key" not in json.dumps(first).lower()


def test_replay_does_not_touch_unrelated_state_directory(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    native = state / "paper_positions.json"
    native.write_bytes(b'{"balance_usd":12345}')
    before = {path.name: path.read_bytes() for path in state.iterdir()}

    replay_llm_lab.run_replay(replay_llm_lab.default_fixture())

    after = {path.name: path.read_bytes() for path in state.iterdir()}
    assert after == before


def test_replay_cli_accepts_recorded_fixture_without_network(tmp_path, capsys):
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(replay_llm_lab.default_fixture()), encoding="utf-8")

    assert replay_llm_lab.main(["--input", str(fixture_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["offline"] is True
    assert payload["model_calls"] == 0


def test_llm_execution_surface_contains_no_private_exchange_order_method():
    root = Path(__file__).resolve().parents[1]
    sources = [
        *(root / "orum/llm").glob("*.py"),
        root / "scripts/run_llm_lab.py",
        root / "scripts/replay_llm_lab.py",
    ]
    forbidden = (
        "create_order(", "create_market_order(", "create_limit_order(",
        "fetch_balance(", "cancel_order(", "apiSecret", "api_secret",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    assert not any(token in combined for token in forbidden)
