from datetime import UTC, datetime

import pytest

from orum.llm.config import LlmMode, LlmTradingConfig
from orum.llm.contracts import MarketBrief, ProposedDecision
from orum.llm.journal import JsonlJournal
from orum.llm.paper_runtime import PaperExecutionResult
from orum.llm.runtime import LlmLabRuntime, LlmRuntimeError
from orum.llm.services import DecisionResult
from orum.llm.snapshot import MarketSnapshotBuilder
from scripts import run_llm_lab


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def test_non_json_derivatives_degrade_to_explicit_unavailable_status():
    class InvalidDerivatives:
        @staticmethod
        def to_mapping():
            return {"funding_rate": float("nan")}

    assert run_llm_lab._safe_derivatives_payload(InvalidDerivatives()) == {
        "status": "unavailable",
        "error": "invalid_derivatives_snapshot",
    }


def test_refresh_paper_accounts_rebuilds_from_immutable_snapshot_mappings(monkeypatch):
    original = _snapshot()
    monkeypatch.setattr(
        run_llm_lab,
        "_read_paper_account",
        lambda: {"status": "available", "native": {"balance_usd": 10_000}},
    )

    refreshed = run_llm_lab._refresh_paper_accounts(original)

    assert dict(refreshed.derivatives) == dict(original.derivatives)
    assert refreshed.paper_account["status"] == "available"


def _snapshot():
    return MarketSnapshotBuilder(clock=lambda: NOW).build(
        cutoff=NOW,
        symbol="BTC/USDT",
        candles={
            "15m": [
                {
                    "ts": int(datetime(2026, 7, 13, 11, 30, tzinfo=UTC).timestamp() * 1000),
                    "open": 100_000,
                    "high": 101_000,
                    "low": 99_500,
                    "close": 100_800,
                    "volume": 100,
                }
            ]
        },
        indicators={"15m": {"rsi": 55}},
        derivatives={"status": "available", "funding_rate": 0.0001},
        macro={},
        onchain={},
        evidence=[],
        paper_account={"status": "unavailable", "positions": []},
    )


def _brief(snapshot):
    return MarketBrief.from_mapping(
        {
            "brief_id": "brief-1",
            "created_at": NOW.isoformat(),
            "snapshot_id": snapshot.snapshot_id,
            "bias": "bullish",
            "regime": "volatile_range",
            "horizons": ["15m", "4h"],
            "facts": ["Price holds the range midpoint"],
            "evidence_completeness": 0.5,
            "evidence_freshness": "partial",
            "narrative_vs_price": "No decisive divergence",
            "interpretation": "Buyers still absorb dips",
            "pain_trade": "A squeeze above the range",
            "main_scenario": "Range continuation",
            "alternate_scenarios": ["Breakdown"],
            "catalysts": [],
            "confidence": 0.6,
            "invalidation": "A closed 4h candle below the range",
            "memo_fr": "Le marché reste constructif mais incomplet.",
            "evidence_ids": [],
        }
    )


def _decision(lane, decision_id):
    return ProposedDecision.from_mapping(
        {
            "decision_id": decision_id,
            "created_at": NOW.isoformat(),
            "lane": lane,
            "symbol": "BTC/USDT",
            "horizon": "4h",
            "action": "hold",
            "equity_fraction": 0,
            "requested_leverage": 0,
            "order_type": "market",
            "limit_price": None,
            "stop_loss": None,
            "take_profits": [],
            "trailing_stop_pct": None,
            "time_exit_minutes": None,
            "confidence": 0.55,
            "thesis": "No sufficiently asymmetric entry yet",
            "counter_thesis": "A fast squeeze can leave the model behind",
            "risk_rationale": "Waiting avoids a poor entry inside the range",
            "invalidation": "A confirmed range break changes the setup",
            "memo_fr": "Je reste en attente d'une cassure confirmée.",
            "evidence_ids": [],
            "lesson_ids": [],
        }
    )


class SnapshotFactory:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.snapshot


class Analyst:
    def __init__(self, brief):
        self.brief = brief
        self.calls = []

    def analyze(self, snapshot):
        self.calls.append(snapshot.snapshot_id)
        return self.brief


class Trader:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def decide(self, snapshot, brief, *, lane, lessons):
        self.calls.append(
            {
                "snapshot_id": snapshot.snapshot_id,
                "brief_id": brief.brief_id,
                "lane": lane,
                "lessons": list(lessons),
            }
        )
        return DecisionResult(decision=self.decision, leverage=None)


class PoisonDependency:
    def __getattr__(self, name):
        raise AssertionError(f"dependency must not be touched: {name}")

    def __call__(self, *args, **kwargs):
        raise AssertionError("dependency must not be called")


class PaperExecutor:
    def __init__(self):
        self.calls = []
        self.monitor_calls = []
        self.processed = set()

    def decision_status(self, *, lane, decision_id):
        return "executed" if (lane, decision_id) in self.processed else None

    def monitor(self, **kwargs):
        self.monitor_calls.append(kwargs)
        return ()

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        decision = kwargs["decision"]
        self.processed.add((decision.lane, decision.decision_id))
        return PaperExecutionResult(
            decision_id=decision.decision_id,
            lane=decision.lane,
            status="executed",
            reasons=(),
            fill_ids=(),
        )


def _runtime(tmp_path, mode, *, lessons=(), paper_executor=None):
    snapshot = _snapshot()
    factory = SnapshotFactory(snapshot)
    analyst = Analyst(_brief(snapshot))
    reference = Trader(_decision("llm_reference", "dec-reference"))
    evolving = Trader(_decision("llm_evolving", "dec-evolving"))
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    runtime = LlmLabRuntime(
        config=LlmTradingConfig(mode=mode),
        snapshot_factory=factory,
        analyst=analyst,
        reference_trader=reference,
        evolving_trader=evolving,
        decision_journal=journal,
        lesson_provider=lambda snapshot, brief, limit: tuple(lessons)[:limit],
        paper_executor=paper_executor,
    )
    return runtime, factory, analyst, reference, evolving, journal


def test_off_performs_zero_provider_calls_and_zero_writes(tmp_path):
    journal = JsonlJournal(tmp_path / "decisions.jsonl")
    runtime = LlmLabRuntime(
        config=LlmTradingConfig(mode=LlmMode.OFF),
        snapshot_factory=PoisonDependency(),
        analyst=PoisonDependency(),
        reference_trader=PoisonDependency(),
        evolving_trader=PoisonDependency(),
        decision_journal=journal,
        lesson_provider=PoisonDependency(),
    )

    result = runtime.run_once()

    assert result.mode is LlmMode.OFF
    assert result.snapshot_id is None
    assert result.brief_id is None
    assert result.lanes == ()
    assert journal.read() == []
    assert not journal.path.exists()


def test_observer_produces_one_brief_and_never_calls_traders(tmp_path):
    runtime, factory, analyst, reference, evolving, journal = _runtime(
        tmp_path, LlmMode.OBSERVER
    )

    result = runtime.run_once()

    assert factory.calls == 1
    assert analyst.calls == [result.snapshot_id]
    assert result.brief_id == "brief-1"
    assert result.lanes == ()
    assert reference.calls == []
    assert evolving.calls == []
    assert journal.read() == []


def test_shadow_runs_reference_and_evolving_lanes_without_execution(tmp_path):
    lessons = ({"lesson_id": "lesson-1", "text": "Avoid late range entries"},)
    runtime, factory, analyst, reference, evolving, journal = _runtime(
        tmp_path, LlmMode.SHADOW, lessons=lessons
    )

    result = runtime.run_once()

    assert factory.calls == 1
    assert len(analyst.calls) == 1
    assert reference.calls[0]["lessons"] == []
    assert evolving.calls[0]["lessons"] == list(lessons)
    assert [(lane.lane, lane.status, lane.decision_id) for lane in result.lanes] == [
        ("llm_reference", "created", "dec-reference"),
        ("llm_evolving", "created", "dec-evolving"),
    ]
    # Fake traders do not journal; the orchestration layer itself performs no execution writes.
    assert journal.read() == []


def test_duplicate_snapshot_lane_is_skipped_before_trader_call(tmp_path):
    runtime, _, _, reference, evolving, journal = _runtime(tmp_path, LlmMode.SHADOW)
    snapshot = _snapshot()
    for lane, decision_id in (
        ("llm_reference", "existing-reference"),
        ("llm_evolving", "existing-evolving"),
    ):
        journal.append(
            {
                "kind": "proposed_decision",
                "status": "valid",
                "snapshot_id": snapshot.snapshot_id,
                "lane": lane,
                "decision": {"decision_id": decision_id},
            }
        )

    result = runtime.run_once()

    assert reference.calls == []
    assert evolving.calls == []
    assert [(lane.status, lane.decision_id) for lane in result.lanes] == [
        ("skipped_duplicate", "existing-reference"),
        ("skipped_duplicate", "existing-evolving"),
    ]


def test_autonomous_resumes_valid_journaled_decision_after_pre_execution_crash(tmp_path):
    paper = PaperExecutor()
    runtime, _, _, reference, evolving, journal = _runtime(
        tmp_path, LlmMode.PAPER_AUTONOMOUS, paper_executor=paper
    )
    snapshot = _snapshot()
    entry = _decision("llm_reference", "existing-reference").to_mapping()
    entry.update(
        {
            "action": "open_long",
            "equity_fraction": 0.1,
            "requested_leverage": 20,
            "stop_loss": 97_000,
            "take_profits": [{"price": 105_000, "fraction": 1}],
            "time_exit_minutes": 240,
            "memo_fr": "J'ouvre le long paper déjà journalisé.",
        }
    )
    for lane, decision in (
        ("llm_reference", entry),
        ("llm_evolving", _decision("llm_evolving", "existing-evolving").to_mapping()),
    ):
        journal.append(
            {
                "kind": "proposed_decision",
                "status": "valid",
                "snapshot_id": snapshot.snapshot_id,
                "lane": lane,
                "decision": decision,
            }
        )

    result = runtime.run_once()

    assert reference.calls == []
    assert evolving.calls == []
    assert [call["decision"].decision_id for call in paper.calls] == [
        "existing-reference",
        "existing-evolving",
    ]
    assert paper.calls[0]["leverage"].paper_effective == 20
    assert paper.calls[0]["leverage"].fr_retail_eligible == 2
    assert [(lane.status, lane.paper_status) for lane in result.lanes] == [
        ("resumed_existing", "executed"),
        ("resumed_existing", "executed"),
    ]


def test_autonomous_resumes_pending_decision_even_when_restart_snapshot_changed(tmp_path):
    paper = PaperExecutor()
    runtime, _, _, reference, evolving, journal = _runtime(
        tmp_path, LlmMode.PAPER_AUTONOMOUS, paper_executor=paper
    )
    old_snapshot = _snapshot()
    for lane, decision_id in (
        ("llm_reference", "pending-reference"),
        ("llm_evolving", "pending-evolving"),
    ):
        decision = _decision(lane, decision_id).to_mapping()
        journal.append({
            "kind": "proposed_decision", "status": "valid", "lane": lane,
            "snapshot_id": "old-snapshot", "snapshot_hash": "old-hash",
            "snapshot_cutoff": old_snapshot.cutoff.isoformat(),
            "market_price": 99_000, "candle_ts": 1234,
            "decision": decision,
        })

    result = runtime.run_once()

    assert reference.calls == []
    assert evolving.calls == []
    assert [call["snapshot_id"] for call in paper.calls] == ["old-snapshot", "old-snapshot"]
    assert [call["market_price"] for call in paper.calls] == [99_000, 99_000]
    assert [lane.status for lane in result.lanes] == ["resumed_pending", "resumed_pending"]


def test_assisted_mode_remains_refused_before_any_dependency_call(tmp_path):
    runtime = LlmLabRuntime(
        config=LlmTradingConfig(mode=LlmMode.PAPER_ASSISTED),
        snapshot_factory=PoisonDependency(),
        analyst=PoisonDependency(),
        reference_trader=PoisonDependency(),
        evolving_trader=PoisonDependency(),
        decision_journal=JsonlJournal(tmp_path / "decisions.jsonl"),
        lesson_provider=PoisonDependency(),
    )

    with pytest.raises(
        LlmRuntimeError,
        match="paper LLM execution is not installed in foundation phase",
    ):
        runtime.run_once()


def test_paper_autonomous_executes_both_isolated_lanes(tmp_path):
    paper = PaperExecutor()
    runtime, _, _, _, _, _ = _runtime(
        tmp_path, LlmMode.PAPER_AUTONOMOUS, paper_executor=paper
    )

    result = runtime.run_once()

    assert [call["decision"].lane for call in paper.calls] == [
        "llm_reference", "llm_evolving"
    ]
    assert [call["lane"] for call in paper.monitor_calls] == [
        "llm_reference", "llm_evolving"
    ]
    assert [lane.paper_status for lane in result.lanes] == ["executed", "executed"]


def test_autonomous_monitors_every_closed_candle_oldest_to_newest(tmp_path):
    snapshot = MarketSnapshotBuilder(clock=lambda: NOW).build(
        cutoff=NOW,
        symbol="BTC/USDT",
        candles={
            "15m": [
                {"ts": 1_000, "open": 100, "high": 101, "low": 94, "close": 100, "volume": 1},
                {"ts": 2_000, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1},
            ]
        },
        indicators={}, derivatives={}, macro={}, onchain={}, evidence=[],
        paper_account={"status": "unavailable", "positions": []},
    )
    paper = PaperExecutor()
    brief = _brief(snapshot)
    runtime = LlmLabRuntime(
        config=LlmTradingConfig(mode=LlmMode.PAPER_AUTONOMOUS),
        snapshot_factory=lambda: snapshot,
        analyst=Analyst(brief),
        reference_trader=Trader(_decision("llm_reference", "ref")),
        evolving_trader=Trader(_decision("llm_evolving", "evo")),
        decision_journal=JsonlJournal(tmp_path / "decisions.jsonl"),
        lesson_provider=lambda *args: (),
        paper_executor=paper,
    )

    runtime.run_once()

    assert [(call["lane"], call["candle"]["ts"]) for call in paper.monitor_calls] == [
        ("llm_reference", 1_000), ("llm_evolving", 1_000),
        ("llm_reference", 2_000), ("llm_evolving", 2_000),
    ]
    assert {call["candle"]["close_ts"] - call["candle"]["ts"] for call in paper.monitor_calls} == {900_000}


class CliRuntime:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def run_once(self):
        self.calls += 1
        return self.result


def test_cli_requires_api_key_only_for_remote_modes(capsys):
    built = []

    def factory(config, api_key):
        built.append((config.mode, api_key))
        return CliRuntime(
            run_llm_lab.LlmRunResult(config.mode, None, None, ())
        )

    assert run_llm_lab.main(
        ["--mode", "off", "--once"],
        environ={},
        runtime_factory=factory,
    ) == 0
    assert built == [(LlmMode.OFF, None)]

    assert run_llm_lab.main(
        ["--mode", "observer", "--once"],
        environ={},
        runtime_factory=factory,
    ) == 2
    assert built == [(LlmMode.OFF, None)]
    assert "NVIDIA_API_KEY is required" in capsys.readouterr().err


def test_cli_prints_shadow_lane_statuses(capsys):
    result = run_llm_lab.LlmRunResult(
        mode=LlmMode.SHADOW,
        snapshot_id="snap-1",
        brief_id="brief-1",
        lanes=(
            run_llm_lab.LaneRunResult("llm_reference", "created", "dec-1"),
            run_llm_lab.LaneRunResult("llm_evolving", "skipped_duplicate", "dec-2"),
        ),
    )

    exit_code = run_llm_lab.main(
        ["--mode", "shadow", "--once"],
        environ={"NVIDIA_API_KEY": "test-key"},
        runtime_factory=lambda config, api_key: CliRuntime(result),
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "snapshot=snap-1 brief=brief-1" in output
    assert "llm_reference status=created decision=dec-1" in output
    assert "llm_evolving status=skipped_duplicate decision=dec-2" in output
    assert "test-key" not in output


def test_cli_refuses_assisted_mode_before_key_or_runtime_construction(capsys):
    def poison_factory(config, api_key):
        raise AssertionError("runtime must not be constructed")

    exit_code = run_llm_lab.main(
        ["--mode", "paper_assisted", "--once"],
        environ={},
        runtime_factory=poison_factory,
    )

    assert exit_code == 2
    assert (
        capsys.readouterr().err.strip()
        == "paper LLM execution is not installed in foundation phase"
    )


def test_cli_requires_explicit_confirmation_for_autonomous_paper(capsys):
    built = []

    def factory(config, api_key):
        built.append(config.mode)
        return CliRuntime(run_llm_lab.LlmRunResult(config.mode, "snap", "brief", ()))

    assert run_llm_lab.main(
        ["--mode", "paper_autonomous", "--once"],
        environ={"NVIDIA_API_KEY": "test-key"}, runtime_factory=factory,
    ) == 2
    assert built == []
    assert "--confirm-paper" in capsys.readouterr().err

    assert run_llm_lab.main(
        ["--mode", "paper_autonomous", "--once", "--confirm-paper"],
        environ={"NVIDIA_API_KEY": "test-key"}, runtime_factory=factory,
    ) == 0
    assert built == [LlmMode.PAPER_AUTONOMOUS]


def test_cli_loads_optional_yaml_and_applies_explicit_mode_override(tmp_path):
    path = tmp_path / "llm.yaml"
    path.write_text(
        "llm_trading:\n"
        "  mode: 'off'\n"
        "  model: 'deepseek/deepseek-v4-pro'\n"
        "  paper_max_leverage: 33\n",
        encoding="utf-8",
    )
    captured = []

    def factory(config, api_key):
        captured.append(config)
        return CliRuntime(run_llm_lab.LlmRunResult(config.mode, "snap", "brief", ()))

    assert run_llm_lab.main(
        ["--mode", "observer", "--once", "--config", str(path)],
        environ={"NVIDIA_API_KEY": "test-key"},
        runtime_factory=factory,
    ) == 0
    assert captured[0].mode is LlmMode.OBSERVER
    assert captured[0].paper_max_leverage == 33


def test_cli_applies_explicit_provider_model_and_timeout_overrides():
    captured = []

    def factory(config, api_key):
        captured.append((config, api_key))
        return CliRuntime(run_llm_lab.LlmRunResult(config.mode, "snap", "brief", ()))

    assert run_llm_lab.main(
        [
            "--mode", "observer", "--once", "--provider", "nvidia",
            "--model", "nvidia/nemotron-3-ultra-550b-a55b",
            "--request-timeout-seconds", "300",
        ],
        environ={"NVIDIA_API_KEY": "test-key"},
        runtime_factory=factory,
    ) == 0
    config, api_key = captured[0]
    assert config.provider == "nvidia"
    assert config.model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert config.request_timeout_seconds == 300
    assert api_key == "test-key"


def test_snapshot_account_reader_labels_native_and_both_llm_lanes(tmp_path):
    native = tmp_path / "native.json"
    reference = tmp_path / "reference.json"
    evolving = tmp_path / "evolving.json"
    native.write_text('{"balance_usd": 10000}', encoding="utf-8")
    reference.write_text('{"lane":"llm_reference","positions":{}}', encoding="utf-8")
    evolving.write_text('{"lane":"llm_evolving","positions":{}}', encoding="utf-8")

    result = run_llm_lab._read_paper_account(
        native,
        llm_account_paths={"llm_reference": reference, "llm_evolving": evolving},
    )

    assert result["native"]["balance_usd"] == 10_000
    assert result["llm_accounts"]["llm_reference"]["lane"] == "llm_reference"
    assert result["llm_accounts"]["llm_evolving"]["lane"] == "llm_evolving"
