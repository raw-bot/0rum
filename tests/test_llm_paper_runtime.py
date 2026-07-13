from datetime import UTC, datetime

from orum.llm.contracts import ProposedDecision
from orum.llm.journal import JsonlJournal
from orum.llm.leverage import apply_leverage_policy
from orum.llm.paper_runtime import PaperLaneExecutor
from orum.llm.paper_simulator import LlmPaperSimulator
from orum.llm.paper_store import LlmPaperStore
from orum.llm.paper_validator import PaperDecisionValidator


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _decision(stop=97_000):
    return ProposedDecision.from_mapping({
        "decision_id": "dec-1", "created_at": NOW.isoformat(),
        "lane": "llm_reference", "symbol": "BTC/USDT", "horizon": "4h",
        "action": "open_long", "equity_fraction": 0.1, "requested_leverage": 20,
        "order_type": "market", "limit_price": None, "stop_loss": stop,
        "take_profits": [{"price": 105_000, "fraction": 1}],
        "trailing_stop_pct": None, "time_exit_minutes": 240, "confidence": 0.7,
        "thesis": "Absorption", "counter_thesis": "Crowded funding",
        "risk_rationale": "Defined paper loss", "invalidation": "Below stop",
        "memo_fr": "J'ouvre un long paper.", "evidence_ids": [], "lesson_ids": [],
    })


def _leverage():
    return apply_leverage_policy(
        requested=20, paper_min=1, paper_max=40, jurisdiction_profile="fr_retail",
        asset_class="crypto", product_kind="perpetual",
    )


def _executor(tmp_path, *, allow_destructive=False):
    store = LlmPaperStore(
        account_paths={"llm_reference": tmp_path / "reference.json"},
        fills_path=tmp_path / "fills.jsonl",
    )
    audit = JsonlJournal(tmp_path / "paper_audit.jsonl")
    return PaperLaneExecutor(
        store=store,
        simulator=LlmPaperSimulator(
            fee_rate=0, allow_stop_beyond_liquidation=allow_destructive
        ),
        validator=PaperDecisionValidator(
            allow_stop_beyond_liquidation=allow_destructive
        ),
        audit_journal=audit,
        starting_balance_usd=10_000,
        clock=lambda: NOW,
    ), store, audit


def test_executor_validates_persists_and_audits_accepted_decision(tmp_path):
    executor, store, audit = _executor(tmp_path)

    result = executor.execute(
        decision=_decision(), leverage=_leverage(), market_price=100_000,
        snapshot_id="snap-1", snapshot_hash="hash-1", snapshot_cutoff=NOW,
        candle_ts=1_000,
    )

    assert result.status == "executed"
    assert len(result.fill_ids) == 1
    assert store.load("llm_reference", starting_balance_usd=10_000).positions
    assert [row["kind"] for row in audit.read()] == ["paper_validation", "paper_execution"]


def test_rejected_decision_is_visible_and_does_not_mutate_account(tmp_path):
    executor, store, audit = _executor(tmp_path)

    result = executor.execute(
        decision=_decision(stop=94_000), leverage=_leverage(), market_price=100_000,
        snapshot_id="snap-1", snapshot_hash="hash-1", snapshot_cutoff=NOW,
        candle_ts=1_000,
    )

    assert result.status == "rejected"
    assert "stop_at_or_beyond_liquidation" in result.reasons
    assert store.load("llm_reference", starting_balance_usd=10_000).positions == {}
    assert len(audit.read()) == 1


def test_executor_replay_is_idempotent(tmp_path):
    executor, store, _ = _executor(tmp_path)
    kwargs = dict(
        decision=_decision(), leverage=_leverage(), market_price=100_000,
        snapshot_id="snap-1", snapshot_hash="hash-1", snapshot_cutoff=NOW,
        candle_ts=1_000,
    )
    first = executor.execute(**kwargs)
    second = executor.execute(**kwargs)

    assert first.status == "executed"
    assert second.status == "already_processed"
    assert len(store.fills.read()) == 1


def test_monitor_commits_pessimistic_exit_once(tmp_path):
    executor, store, audit = _executor(tmp_path)
    executor.execute(
        decision=_decision(), leverage=_leverage(), market_price=100_000,
        snapshot_id="snap-1", snapshot_hash="hash-1", snapshot_cutoff=NOW,
        candle_ts=1_000,
    )

    first = executor.monitor(
        lane="llm_reference",
        candle={"ts": 2_000, "open": 100_000, "high": 101_000, "low": 96_000, "close": 97_500},
    )
    second = executor.monitor(
        lane="llm_reference",
        candle={"ts": 2_000, "open": 100_000, "high": 101_000, "low": 96_000, "close": 97_500},
    )

    assert [fill.action for fill in first] == ["stop"]
    assert second == ()
    assert store.load("llm_reference", starting_balance_usd=10_000).positions == {}
    assert audit.read()[-1]["kind"] == "paper_monitor"
