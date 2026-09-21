"""Regression guarantees for the 2026-09-05 audit, using synthetic accounts."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import json

import pytest

from orum.llm.contracts import ProposedDecision
from orum.llm.config import LlmMode, LlmTradingConfig
from orum.llm.journal import JsonlJournal
from orum.llm.leverage import apply_leverage_policy
from orum.llm.paper_runtime import PaperLaneExecutor
from orum.llm.paper_simulator import LlmPaperSimulator
from orum.llm.paper_store import LlmPaperStore, PaperStoreError
from orum.llm.paper_validator import PaperDecisionValidator
from orum.llm.runtime import LlmLabRuntime, LlmRunResult, LaneRunResult
from orum.llm.snapshot import MarketSnapshotBuilder
from orum.llm.services import LlmServiceError
from orum.portfolio.paper_broker import Account, Position
from orum.portfolio.paper_engine import PaperEngine
from orum.strategies.base import Signal, Side

NOW = datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
TS = int(NOW.timestamp() * 1000)


def decision(lane="llm_reference", decision_id="audit-decision"):
    return ProposedDecision.from_mapping({
        "decision_id": decision_id, "created_at": NOW.isoformat(),
        "lane": lane, "symbol": "BTC/USDT", "horizon": "4h",
        "action": "open_long", "equity_fraction": .1, "requested_leverage": 20,
        "order_type": "market", "limit_price": None, "stop_loss": 97000,
        "take_profits": [{"price": 105000, "fraction": 1}],
        "trailing_stop_pct": None, "time_exit_minutes": 240, "confidence": .7,
        "thesis": "Synthetic", "counter_thesis": "Synthetic",
        "risk_rationale": "Synthetic", "invalidation": "Synthetic",
        "memo_fr": "Scénario synthétique.", "evidence_ids": [], "lesson_ids": [],
    })


def executor(tmp_path):
    store = LlmPaperStore(account_paths={
        lane: tmp_path / f"{lane}.json" for lane in ("llm_reference", "llm_evolving")
    }, fills_path=tmp_path / "fills.jsonl")
    audit = JsonlJournal(tmp_path / "audit.jsonl")
    obj = PaperLaneExecutor(store=store, simulator=LlmPaperSimulator(),
        validator=PaperDecisionValidator(), audit_journal=audit,
        starting_balance_usd=10000, clock=lambda: NOW)
    return obj, store, audit


def kwargs(lane="llm_reference", decision_id="audit-decision"):
    return dict(decision=decision(lane, decision_id),
        leverage=apply_leverage_policy(requested=20, paper_min=1, paper_max=40,
            jurisdiction_profile="fr_retail", asset_class="crypto", product_kind="perpetual"),
        market_price=100000, snapshot_id="snapshot-audit", snapshot_hash="hash-audit",
        snapshot_cutoff=NOW, candle_ts=TS-900000)


@pytest.mark.parametrize("fault", ["propose", "write"])
def test_persist_1_native_shadow_failure_keeps_protective_close(tmp_path, monkeypatch, fault):
    rows = [{"ts": TS+i*900000, "open": 100, "high": 101,
             "low": 99, "close": 100, "volume": 1} for i in range(20)]
    rows[-1] = {**rows[-1], "low": 89}
    config = {"starting_balance_usd": 10000, "reentry_policy": "hold",
        "strategies": [{"id": name, "engine": "donchian", "symbol": symbol,
            "timeframe": "15m", "monitor_timeframe": "15m", "risk_pct": .01,
            "exit_policy": "structural_bracket", "params": {}}
            for name, symbol in (("protected", "BTC/USDT"), ("candidate", "ETH/USDT"))]}
    engine = PaperEngine(config, candle_provider=lambda *args: rows,
        positions_path=tmp_path/"positions.json", fills_path=tmp_path/"fills.jsonl",
        equity_path=tmp_path/"equity.jsonl")
    engine._engines = {
        "protected": SimpleNamespace(on_candle=lambda *args: None),
        "candidate": SimpleNamespace(on_candle=lambda c, ctx: Signal(
            Side.LONG, ctx.symbol, ctx.timeframe, "synthetic", suggested_stop=80)),
    }
    initial = Account(balance_usd=10000, positions={"protected": Position(
        strategy_id="protected", symbol="BTC/USDT", side="long", qty=1,
        entry_px=100, notional_usd=100, risk_pct=.01, atr_risk=10, risk_distance=10,
        stop_loss_price=90, take_profit_price=120,
        last_monitor_candle_ts=rows[-2]["ts"])})
    engine._save_account(initial)
    before = (tmp_path/"positions.json").read_bytes()
    closed = []
    real_close = engine._broker.close
    def track_close(*args, **kw):
        fill = real_close(*args, **kw)
        if fill: closed.append(fill)
        return fill
    monkeypatch.setattr(engine._broker, "close", track_close)
    def fail_shadow(**kw):
        raise RuntimeError("synthetic observational module failure")
    engine._dynamic_risk_shadow = SimpleNamespace(
        propose=fail_shadow if fault == "propose" else lambda **kw: {})
    if fault == "write":
        real_append = engine._append
        def fail_append(path, record):
            if path == engine._dynamic_risk_shadow_path:
                raise OSError("synthetic observational journal write failure")
            return real_append(path, record)
        monkeypatch.setattr(engine, "_append", fail_append)
    summary = engine.run_cycle(now=NOW+timedelta(hours=5))
    assert any("shadow" in key for key in summary["errors"])
    assert len(closed) == 1 and closed[0]["action"] == "close"
    assert (tmp_path/"positions.json").read_bytes() != before
    assert "protected" not in engine._load_account().positions
    assert (tmp_path/"fills.jsonl").exists()


def test_persist_2_llm_uncommitted_crash_then_stale_retry_rejects_without_fill(tmp_path, monkeypatch):
    obj, store, audit = executor(tmp_path)
    store.ensure_account("llm_reference", starting_balance_usd=10000)
    def fail_write(*args):
        raise PaperStoreError("synthetic crash after durable fill before account replace")
    with monkeypatch.context() as patch:
        patch.setattr(store, "_atomic_account_write", fail_write)
        with pytest.raises(PaperStoreError, match="synthetic crash"):
            obj.execute(**kwargs())
    assert len(store.fills.read()) == 0
    obj._clock = lambda: NOW + timedelta(hours=1)
    retry = obj.execute(**kwargs())
    persisted = store.load("llm_reference", starting_balance_usd=10000)
    assert retry.status == "rejected" and "snapshot_stale" in retry.reasons
    assert persisted.positions == {} and persisted.balance_usd == 10000
    assert store.fills.read() == []
    assert obj.decision_status(lane="llm_reference", decision_id="audit-decision") == "rejected"


def test_positive_immediate_llm_crash_retry_is_idempotent(tmp_path, monkeypatch):
    obj, store, audit = executor(tmp_path)
    def fail_write(*args): raise PaperStoreError("synthetic crash")
    with monkeypatch.context() as patch:
        patch.setattr(store, "_atomic_account_write", fail_write)
        with pytest.raises(PaperStoreError): obj.execute(**kwargs())
    assert obj.execute(**kwargs()).status == "executed"
    assert obj.execute(**kwargs()).status == "already_processed"
    assert len(store.fills.read()) == 1


def test_persist_3_learning_exception_preserves_both_lanes_stop(tmp_path):
    obj, store, audit = executor(tmp_path)
    for lane in ("llm_reference", "llm_evolving"):
        obj.execute(**kwargs(lane, "audit-"+lane))
    cutoff = NOW+timedelta(minutes=15)
    snapshot = MarketSnapshotBuilder(clock=lambda: cutoff).build(
        cutoff=cutoff, symbol="BTC/USDT", candles={"15m": [{"ts": TS,
            "open": 100000, "high": 100500, "low": 96000, "close": 97500,
            "volume": 1}]}, indicators={}, derivatives={}, macro={}, onchain={},
        evidence=[], paper_account={})
    def fail_learning(**kw): raise LlmServiceError("synthetic postmortem provider failure")
    runtime = LlmLabRuntime(config=LlmTradingConfig(mode=LlmMode.PAPER_AUTONOMOUS),
        snapshot_factory=lambda: snapshot, analyst=SimpleNamespace(analyze=lambda s: (_ for _ in ()).throw(LlmServiceError("postmortem test stops after monitoring"))), reference_trader=None,
        evolving_trader=None, decision_journal=audit, lesson_provider=lambda *args: (),
        paper_executor=obj, learning_processor=SimpleNamespace(process=fail_learning))
    with pytest.raises(LlmServiceError, match="postmortem"):
        runtime.run_once()
    assert not store.load("llm_reference", starting_balance_usd=10000).positions
    assert not store.load("llm_evolving", starting_balance_usd=10000).positions
    assert [x["lane"] for x in store.fills.read() if x["action"] == "stop"] == ["llm_reference", "llm_evolving"]


def test_persist_4_failed_model_lanes_publish_cycle_error(tmp_path):
    from scripts.run_llm_lab import main
    from orum.llm.paper_agent import run_paper_cycle
    def runtime_factory(config, api_key):
        return SimpleNamespace(run_once=lambda: LlmRunResult(
            LlmMode.PAPER_AUTONOMOUS, "synthetic", "synthetic", tuple(
                LaneRunResult(lane, "model_error", None, error="synthetic provider failure")
                for lane in ("llm_reference", "llm_evolving"))))
    def run_once(argv, *, environ):
        return main(argv, environ=environ, runtime_factory=runtime_factory)
    status_path=tmp_path/"runtime_status.json"
    code = run_paper_cycle(status_path=status_path, key_loader=lambda: "synthetic-no-network",
        run_once=run_once, now=lambda: NOW)
    status=json.loads(status_path.read_text())
    assert code != 0 and status["last_result"] != "ok" and status["last_error"]

@pytest.mark.parametrize("boundary", ["before_publish", "after_publish", "clear_outbox", "torn_ascii", "torn_unicode", "missing_newline"])
def test_committed_llm_transaction_recovers_before_hourly_stale_validation(tmp_path, monkeypatch, boundary):
    obj, store, _ = executor(tmp_path)
    publish = store.fills.publish_pending
    write = store._atomic_account_write
    def faulty_publish(records):
        if boundary == "after_publish":
            publish(records)
        elif boundary.startswith("torn") or boundary == "missing_newline":
            payload = json.dumps(records[0], allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            fragment = payload if boundary == "missing_newline" else payload[:30]
            store.fills.path.write_bytes(fragment)
        raise OSError("synthetic crash")
    def faulty_clear(path, account):
        if not account.pending_fills:
            raise OSError("synthetic crash clearing outbox")
        write(path, account)
    with monkeypatch.context() as patch:
        if boundary == "clear_outbox":
            patch.setattr(store, "_atomic_account_write", faulty_clear)
        else:
            patch.setattr(store.fills, "publish_pending", faulty_publish)
        with pytest.raises(OSError):
            obj.execute(**kwargs())
    obj._clock = lambda: NOW + timedelta(hours=1)
    result = obj.execute(**kwargs())
    assert result.status == "already_processed"
    account = store.load("llm_reference", starting_balance_usd=10000)
    assert account.balance_usd == 9990
    assert len(account.positions) == 1 and not account.pending_fills
    assert len(store.fills.read()) == 1
    fresh = obj.execute(**kwargs(decision_id="new-stale"))
    assert fresh.status == "rejected" and "snapshot_stale" in fresh.reasons
    if boundary.startswith("torn"):
        assert len(list(tmp_path.glob("fills.jsonl.torn-*"))) == 1


def test_legacy_orphan_fails_closed_without_rewriting(tmp_path):
    obj, store, _ = executor(tmp_path)
    obj.execute(**kwargs())
    path = store.account_paths["llm_reference"]
    raw = json.loads(path.read_text())
    raw.update(schema_version=1, balance_usd=10000, positions={}, processed_decision_ids=[])
    raw.pop("pending_fills")
    path.write_text(json.dumps(raw))
    evidence = path.read_bytes(), store.fills.path.read_bytes()
    with pytest.raises(PaperStoreError):
        obj.execute(**kwargs())
    assert evidence == (path.read_bytes(), store.fills.path.read_bytes())


@pytest.mark.parametrize('fault',['positions','decisions','cursor','missing'])
def test_legacy_equal_balance_or_missing_account_fails_closed(tmp_path, fault):
    obj, store, _=executor(tmp_path)
    obj.execute(**kwargs())
    path=store.account_paths['llm_reference']
    raw=json.loads(path.read_text());raw.update(schema_version=1);raw.pop('pending_fills')
    if fault=='positions': raw['positions']={}
    if fault=='decisions': raw['processed_decision_ids']=[]
    if fault=='cursor': raw['last_processed_candles']={}
    path.write_text(json.dumps(raw))
    if fault=='missing': path.unlink()
    before=path.read_bytes() if path.exists() else None
    ledger=store.fills.path.read_bytes()
    with pytest.raises(PaperStoreError): store.ensure_account('llm_reference',starting_balance_usd=10000)
    assert (path.read_bytes() if path.exists() else None)==before
    assert store.fills.path.read_bytes()==ledger


def test_legacy_lane_startup_recovers_other_lanes_torn_outbox(tmp_path, monkeypatch):
    obj,store,_=executor(tmp_path)
    reference=store.ensure_account('llm_reference',starting_balance_usd=10000)
    path=store.account_paths['llm_reference']
    raw=reference.to_mapping();raw.update(schema_version=1);raw.pop('pending_fills')
    path.write_text(json.dumps(raw))
    def partial(records):
        payload=json.dumps(records[0],allow_nan=False,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()
        store.fills.path.write_bytes(payload[:30]);raise OSError('crash')
    with monkeypatch.context() as m:
        m.setattr(store.fills,'publish_pending',partial)
        with pytest.raises(OSError): obj.execute(**kwargs('llm_evolving'))
    assert store.ensure_account('llm_reference',starting_balance_usd=10000).positions=={}
    assert len(store.fills.read())==1
    assert not store.load('llm_evolving',starting_balance_usd=10000).pending_fills


def test_journal_recovers_actual_truncated_utf8(tmp_path):
    journal=JsonlJournal(tmp_path/'fills.jsonl')
    row={'operation_id':'unicode','reason':'entrée'}
    payload=json.dumps(row,allow_nan=False,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()
    journal.path.write_bytes(payload[:payload.index('é'.encode())+1])
    journal.publish_pending([row])
    assert journal.read()==[row]


@pytest.mark.parametrize('already_committed',[True,False])
def test_operation_conflict_rejected_before_account_mutation(tmp_path,already_committed):
    from orum.llm.paper_simulator import SimulationResult
    from orum.llm.paper_contracts import LlmPaperFill
    obj,store,_=executor(tmp_path)
    before=store.ensure_account('llm_reference',starting_balance_usd=10000)
    obj.execute(**kwargs())
    after=store.load('llm_reference',starting_balance_usd=10000)
    fill=LlmPaperFill.from_mapping(store.fills.read()[0])
    changed=replace(fill,price=fill.price+1)
    account=after if already_committed else replace(after,balance_usd=after.balance_usd-1)
    evidence=store.account_paths['llm_reference'].read_bytes(),store.fills.path.read_bytes()
    with pytest.raises(PaperStoreError,match='payload conflict'):
        store.commit(before,SimulationResult(account,(changed,)))
    assert evidence==(store.account_paths['llm_reference'].read_bytes(),store.fills.path.read_bytes())


def test_failed_llm_monitor_keeps_earliest_candle_and_other_lane_protected(tmp_path,monkeypatch):
    obj,store,audit=executor(tmp_path)
    for lane in ('llm_reference','llm_evolving'): obj.execute(**kwargs(lane,'audit-'+lane))
    cutoff=NOW+timedelta(minutes=30)
    rows=[dict(ts=TS,open=100000,high=100500,low=96000,close=97500,volume=1),
          dict(ts=TS+900000,open=99000,high=100000,low=98000,close=99000,volume=1)]
    snapshot=MarketSnapshotBuilder(clock=lambda:cutoff).build(cutoff=cutoff,symbol='BTC/USDT',candles={'15m':rows},indicators={},derivatives={},macro={},onchain={},evidence=[],paper_account={})
    def halt(snapshot): raise LlmServiceError('stop after monitoring')
    runtime=LlmLabRuntime(config=LlmTradingConfig(mode=LlmMode.PAPER_AUTONOMOUS),snapshot_factory=lambda:snapshot,analyst=SimpleNamespace(analyze=halt),reference_trader=None,evolving_trader=None,decision_journal=audit,lesson_provider=lambda *a:(),paper_executor=obj)
    write=store._atomic_account_write
    failed=[]
    def fail_once(path,account):
        if account.lane=='llm_reference' and account.pending_fills and not failed:
            failed.append(True);raise OSError('pre-replace failure')
        return write(path,account)
    with monkeypatch.context() as m:
        m.setattr(store,'_atomic_account_write',fail_once)
        with pytest.raises(LlmServiceError):runtime.run_once()
    reference=store.load('llm_reference',starting_balance_usd=10000)
    assert reference.positions and max(reference.last_processed_candles.values())<TS
    assert not store.load('llm_evolving',starting_balance_usd=10000).positions
    with pytest.raises(LlmServiceError):runtime.run_once()
    stops=[row for row in store.fills.read() if row['action']=='stop']
    assert len(stops)==2 and all(row['candle_ts']==TS for row in stops)


def test_learning_refuses_partial_history_before_publishing_outcome(tmp_path):
    from test_llm_learning import _fill,_snapshot,OPEN_TS,CLOSE_TS
    from orum.llm.learning import LearningProcessor
    fills=JsonlJournal(tmp_path/'learning-fills.jsonl')
    opening=_fill('open',OPEN_TS-900000,100000)
    close=_fill('stop',CLOSE_TS,97000)
    fills.append(opening.to_mapping());fills.append(close.to_mapping())
    decisions=JsonlJournal(tmp_path/'decisions.jsonl')
    decisions.append({'kind':'proposed_decision','decision':{'decision_id':'dec-1'}})
    outcomes=JsonlJournal(tmp_path/'outcomes.jsonl')
    snapshot=_snapshot()
    snapshot=replace(snapshot,candles={'15m':snapshot.candles['15m'][-1:]})
    processor=LearningProcessor(fills=fills,decisions=decisions,briefs=JsonlJournal(tmp_path/'briefs.jsonl'),outcomes=outcomes,evaluator=None,postmortem=None,lessons=SimpleNamespace(for_decision=lambda _:None),decision_timeframe='15m')
    assert processor.process(fill=close,snapshot=snapshot).status=='incomplete_candles'
    assert outcomes.read()==[]
