"""Crash-recoverable persistence for isolated LLM paper lane accounts."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import fcntl
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from orum.llm.journal import JsonlJournal
from orum.llm.paper_contracts import LlmPaperAccount
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
        self.fills = JsonlJournal(fills_path)
        self._lock = threading.RLock()
        self._lock_path = Path(f"{fills_path}.lock")

    def load(self, lane: str, *, starting_balance_usd: float) -> LlmPaperAccount:
        path = self._path(lane)
        if not path.exists():
            return LlmPaperAccount.from_mapping(
                None, lane=lane, starting_balance_usd=starting_balance_usd
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return LlmPaperAccount.from_mapping(raw)
        except Exception as exc:  # noqa: BLE001 - corrupt financial state fails closed
            raise PaperStoreError(f"cannot load {lane} paper account: {type(exc).__name__}") from exc

    def ensure_account(self, lane: str, *, starting_balance_usd: float) -> LlmPaperAccount:
        with self._lock, self._process_lock():
            path = self._path(lane)
            if path.exists():
                return self.load(lane, starting_balance_usd=starting_balance_usd)
            account = LlmPaperAccount.from_mapping(
                None, lane=lane, starting_balance_usd=starting_balance_usd
            )
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
            current = self.load(lane, starting_balance_usd=before.starting_balance_usd)
            existing_records = self.fills.read()
            existing_operations = {
                record.get("operation_id") for record in existing_records
                if isinstance(record.get("operation_id"), str)
            }
            if current.to_mapping() == result.account.to_mapping() and all(
                fill.operation_id in existing_operations for fill in result.fills
            ):
                return current
            if current.to_mapping() != before.to_mapping():
                raise PaperStoreError("stale prior paper account; reload before committing")
            for fill in result.fills:
                if fill.lane != lane:
                    raise PaperStoreError("fill lane does not match paper account")
                if fill.operation_id not in existing_operations:
                    self.fills.append(fill.to_mapping())
                    existing_operations.add(fill.operation_id)
            self._atomic_account_write(self._path(lane), result.account)
            return result.account

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
