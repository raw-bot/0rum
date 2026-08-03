import json
from datetime import UTC, datetime

from orum.llm.paper_agent import main, run_paper_cycle


NOW = datetime(2026, 7, 13, 20, 0, tzinfo=UTC)


def test_cycle_uses_memory_only_secret_and_publishes_redacted_success(tmp_path):
    seen = {}

    def run_once(argv, *, environ):
        seen["argv"] = argv
        seen["key"] = environ["NVIDIA_API_KEY"]
        return 0

    status_path = tmp_path / "status.json"
    code = run_paper_cycle(
        status_path=status_path,
        key_loader=lambda: "memory-only-secret",
        run_once=run_once,
        now=lambda: NOW,
    )

    assert code == 0
    assert seen == {
        "argv": [
            "--mode", "paper_autonomous", "--once", "--confirm-paper",
            "--provider", "nvidia", "--model", "nvidia/nemotron-3-ultra-550b-a55b",
            "--request-timeout-seconds", "300",
        ],
        "key": "memory-only-secret",
    }
    status_text = status_path.read_text(encoding="utf-8")
    status = json.loads(status_text)
    assert status == {
        "enabled": True,
        "interval_minutes": 60,
        "last_cycle_completed_at": NOW.isoformat(),
        "last_cycle_started_at": NOW.isoformat(),
        "last_error": "",
        "last_result": "ok",
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "running": False,
    }
    assert "memory-only-secret" not in status_text


def test_keychain_failure_is_redacted_and_model_is_not_called(tmp_path):
    called = False

    def fail_key():
        raise RuntimeError("sensitive-keychain-provider-output")

    def run_once(argv, *, environ):
        nonlocal called
        called = True
        return 0

    status_path = tmp_path / "status.json"
    code = run_paper_cycle(
        status_path=status_path,
        key_loader=fail_key,
        run_once=run_once,
        now=lambda: NOW,
    )

    status_text = status_path.read_text(encoding="utf-8")
    status = json.loads(status_text)
    assert code == 2
    assert called is False
    assert status["last_result"] == "configuration_error"
    assert status["last_error"] == "NVIDIA credential unavailable"
    assert status["running"] is False
    assert "sensitive-keychain-provider-output" not in status_text


def test_nonzero_lab_exit_is_bounded_and_keeps_error_redacted(tmp_path):
    status_path = tmp_path / "status.json"
    code = run_paper_cycle(
        status_path=status_path,
        key_loader=lambda: "secret",
        run_once=lambda argv, *, environ: 1,
        now=lambda: NOW,
    )

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert code == 1
    assert status["last_result"] == "cycle_error"
    assert status["last_error"] == "LLM paper cycle exited with status 1"


def test_disabled_main_returns_before_keychain_or_state_access(tmp_path):
    called = False

    def key_loader():
        nonlocal called
        called = True
        return "secret"

    status_path = tmp_path / "status.json"
    code = main(
        environ={"LLM_PAPER_ENABLED": "0"},
        status_path=status_path,
        key_loader=key_loader,
    )

    assert code == 0
    assert called is False
    assert status_path.exists() is False
