from datetime import UTC, datetime

import pytest

from orum.llm.paper_contracts import LlmPaperAccount, LlmPaperFill
from orum.llm.paper_simulator import SimulationResult
from orum.llm.paper_store import LlmPaperStore, PaperStoreError


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _account(balance=10_000, processed=()):
    return LlmPaperAccount(
        lane="llm_reference", starting_balance_usd=10_000,
        balance_usd=balance, processed_decision_ids=processed,
    )


def _fill():
    return LlmPaperFill(
        fill_id="fill-1", operation_id="op-1", decision_id="dec-1",
        position_id="pos-1", lane="llm_reference", symbol="BTC/USDT",
        action="open", reason="llm_decision", side="long", qty=0.1,
        price=100_000, fee_usd=5, realized_pnl_usd=0,
        balance_after_usd=9_995, candle_ts=1_000, created_at=NOW,
    )


def test_store_atomically_round_trips_lane_account_and_fill(tmp_path):
    store = LlmPaperStore(
        account_paths={"llm_reference": tmp_path / "account.json"},
        fills_path=tmp_path / "fills.jsonl",
    )
    after = _account(balance=9_995, processed=("dec-1",))

    committed = store.commit(_account(), SimulationResult(after, (_fill(),)))

    assert committed == after
    assert store.load("llm_reference", starting_balance_usd=10_000) == after
    assert store.fills.read() == [_fill().to_mapping()]


def test_repeated_commit_is_idempotent_and_does_not_duplicate_fee_or_fill(tmp_path):
    store = LlmPaperStore(
        account_paths={"llm_reference": tmp_path / "account.json"},
        fills_path=tmp_path / "fills.jsonl",
    )
    result = SimulationResult(_account(balance=9_995, processed=("dec-1",)), (_fill(),))
    store.commit(_account(), result)

    repeated = store.commit(_account(), result)

    assert repeated.balance_usd == 9_995
    assert len(store.fills.read()) == 1


def test_store_rejects_stale_before_state_and_lane_mismatch(tmp_path):
    store = LlmPaperStore(
        account_paths={"llm_reference": tmp_path / "account.json"},
        fills_path=tmp_path / "fills.jsonl",
    )
    store.commit(
        _account(), SimulationResult(_account(balance=9_995, processed=("dec-1",)), (_fill(),))
    )

    with pytest.raises(PaperStoreError, match="stale"):
        store.commit(
            _account(),
            SimulationResult(_account(balance=9_990, processed=("dec-2",)), ()),
        )


def test_corrupt_account_fails_closed(tmp_path):
    path = tmp_path / "account.json"
    path.write_text("{broken", encoding="utf-8")
    store = LlmPaperStore(
        account_paths={"llm_reference": path}, fills_path=tmp_path / "fills.jsonl"
    )

    with pytest.raises(PaperStoreError, match="cannot load"):
        store.load("llm_reference", starting_balance_usd=10_000)
