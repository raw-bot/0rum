"""Crash-recoverable persistence for isolated LLM paper lane accounts."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import fcntl
from dataclasses import replace
from math import isclose
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from orum.llm.journal import JsonlJournal
from orum.llm.paper_contracts import LlmPaperAccount, LlmPaperFill
from orum.llm.paper_simulator import SimulationResult


class PaperStoreError(RuntimeError):
    """Raised when paper state cannot be reconciled or persisted safely."""


class LlmPaperStore:
    def __init__(
        self,
        *,
        account_paths: Mapping[str, Path],
        fills_path: Path,
    ) -> None:
        self.account_paths = {lane: Path(path) for lane, path in account_paths.items()}
        if len({path.resolve() for path in self.account_paths.values()}) != len(self.account_paths):
            raise PaperStoreError("paper lanes must have distinct account paths")
        self.fills = JsonlJournal(fills_path)
        self._lock = threading.RLock()
        self._lock_path = Path(f"{fills_path}.lock")

    def load(self, lane: str, *, starting_balance_usd: float) -> LlmPaperAccount:
        with self._lock, self._process_lock():
            self._recover_pending_accounts()
            return self._recover(self._load(lane, starting_balance_usd=starting_balance_usd))

    def _load(self, lane: str, *, starting_balance_usd: float) -> LlmPaperAccount:
        path = self._path(lane)
        if not path.exists():
            if any(row.get("lane") == lane for row in self.fills.read()):
                raise PaperStoreError("missing account with durable lane fills; reconciliation required")
            return LlmPaperAccount.from_mapping(
                None, lane=lane, starting_balance_usd=starting_balance_usd
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            account = LlmPaperAccount.from_mapping(raw)
            if account.lane != lane:
                raise PaperStoreError("account lane mismatch")
            if raw.get("schema_version", 1) == 1:
                self._validate_legacy(account)
            return account
        except Exception as exc:  # noqa: BLE001 - corrupt financial state fails closed
            raise PaperStoreError(f"cannot load {lane} paper account: {type(exc).__name__}") from exc

    def ensure_account(self, lane: str, *, starting_balance_usd: float) -> LlmPaperAccount:
        with self._lock, self._process_lock():
            self._recover_pending_accounts()
            path = self._path(lane)
            if path.exists():
                return self._recover(self._load(lane, starting_balance_usd=starting_balance_usd))
            account = self._load(lane, starting_balance_usd=starting_balance_usd)
            self._atomic_account_write(path, account)
            return account

    def commit(
        self,
        before: LlmPaperAccount,
        result: SimulationResult,
    ) -> LlmPaperAccount:
        lane = before.lane
        if result.account.lane != lane:
            raise PaperStoreError("result lane does not match prior account")
        if lane not in self.account_paths:
            raise PaperStoreError(f"unknown paper lane {lane!r}")
        with self._lock, self._process_lock():
            self._recover_pending_accounts()
            current = self._recover(self._load(lane, starting_balance_usd=before.starting_balance_usd))
            existing_records = self.fills.read()
            existing_operations = {
                record.get("operation_id") for record in existing_records
                if isinstance(record.get("operation_id"), str)
            }
            by_operation = {record.get("operation_id"): record for record in existing_records}
            for fill in result.fills:
                existing = by_operation.get(fill.operation_id)
                if existing is not None and existing != fill.to_mapping():
                    raise PaperStoreError("operation payload conflict; reconciliation required")
            if current.to_mapping() == result.account.to_mapping() and all(
                fill.operation_id in existing_operations for fill in result.fills
            ):
                return current
            if current.to_mapping() != before.to_mapping():
                raise PaperStoreError("stale prior paper account; reload before committing")
            for fill in result.fills:
                if fill.lane != lane:
                    raise PaperStoreError("fill lane does not match paper account")
            if any(fill.operation_id in existing_operations for fill in result.fills):
                raise PaperStoreError("operation already published with different account state")
            if result.account.pending_fills:
                raise PaperStoreError("simulation may not supply a pending outbox")
            durable = replace(result.account, pending_fills=tuple(result.fills))
            self._atomic_account_write(self._path(lane), durable)
            return self._recover(durable)

    def _validate_legacy(self, account: LlmPaperAccount) -> None:
        records = [LlmPaperFill.from_mapping(row) for row in self.fills.read() if row.get("lane") == account.lane]
        if records and not isclose(records[-1].balance_after_usd, account.balance_usd, rel_tol=0, abs_tol=1e-8):
            raise PaperStoreError("legacy fill/account balance mismatch; reconciliation required")
        quantities: dict[str, float] = {}
        operations: set[str] = set()
        for fill in records:
            if fill.operation_id in operations or fill.decision_id not in account.processed_decision_ids:
                raise PaperStoreError("legacy fill/account operation mismatch; reconciliation required")
            operations.add(fill.operation_id)
            quantities[fill.position_id] = quantities.get(fill.position_id, 0) + (fill.qty if fill.action in {"open", "add"} else -fill.qty)
            if fill.action in {"open", "stop", "take_profit", "liquidation", "time_exit"} and account.last_processed_candles.get(fill.position_id, -1) < fill.candle_ts:
                raise PaperStoreError("legacy fill/account cursor mismatch; reconciliation required")
        actual = {position.position_id: position.qty for position in account.positions.values()}
        if any(not isclose(quantities.get(key, 0), actual.get(key, 0), rel_tol=1e-9, abs_tol=1e-10) for key in quantities.keys() | actual.keys()):
            raise PaperStoreError("legacy fill/account position mismatch; reconciliation required")

    def _recover_pending_accounts(self) -> None:
        # A different lane may own the torn tail of the shared journal. Restore
        # all committed outboxes before a legacy lane reads that journal.
        for lane, path in self.account_paths.items():
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise PaperStoreError(f"cannot load {lane} paper account: {type(exc).__name__}") from exc
            if raw.get("pending_fills"):
                account = LlmPaperAccount.from_mapping(raw)
                if account.lane != lane or raw.get("schema_version") != 2:
                    raise PaperStoreError("pending account lane/schema mismatch")
                self._recover(account)

    def _recover(self, account: LlmPaperAccount) -> LlmPaperAccount:
        # Caller holds the process lock. A committed account is authoritative;
        # publish its outbox before accepting any new decision, including stale retries.
        if not account.pending_fills:
            return account
        self.fills.publish_pending([fill.to_mapping() for fill in account.pending_fills])
        clean = replace(account, pending_fills=())
        self._atomic_account_write(self._path(account.lane), clean)
        return clean

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _path(self, lane: str) -> Path:
        try:
            return self.account_paths[lane]
        except KeyError as exc:
            raise PaperStoreError(f"unknown paper lane {lane!r}") from exc

    @staticmethod
    def _atomic_account_write(path: Path, account: LlmPaperAccount) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            account.to_mapping(), allow_nan=False, ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise PaperStoreError(f"cannot persist paper account {path}: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
