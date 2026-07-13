"""Crash-recoverable persistence for isolated LLM paper lane accounts."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path

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
        self._locks = {lane: threading.Lock() for lane in self.account_paths}

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

    def commit(
        self,
        before: LlmPaperAccount,
        result: SimulationResult,
    ) -> LlmPaperAccount:
        lane = before.lane
        if result.account.lane != lane:
            raise PaperStoreError("result lane does not match prior account")
        lock = self._locks.get(lane)
        if lock is None:
            raise PaperStoreError(f"unknown paper lane {lane!r}")
        with lock:
            current = self.load(lane, starting_balance_usd=before.starting_balance_usd)
            existing_records = self.fills.read()
            existing_operations = {
                record.get("operation_id") for record in existing_records
                if isinstance(record.get("operation_id"), str)
            }
            desired_decisions = set(result.account.processed_decision_ids)
            if desired_decisions.issubset(current.processed_decision_ids) and all(
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
        except OSError as exc:
            raise PaperStoreError(f"cannot persist paper account {path}: {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
