"""Durable append-only JSONL journals for every LLM observation and decision."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class JournalError(RuntimeError):
    """Raised when a journal record cannot be durably written or decoded."""


_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    resolved = path.expanduser().resolve()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(resolved, threading.Lock())


class JsonlJournal:
    """Append JSON objects as fsynced lines and read bounded history."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = _lock_for(self.path)

    def append(self, record: Mapping[str, Any]) -> None:
        if not isinstance(record, Mapping):
            raise JournalError("journal record must be a JSON object")
        try:
            line = json.dumps(
                dict(record),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise JournalError(f"journal record is not JSON-safe: {exc}") from exc

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise JournalError(f"cannot append journal {self.path}: {exc}") from exc

    def read(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None and (isinstance(limit, bool) or limit <= 0):
            raise JournalError("limit must be a positive integer")
        with self._lock:
            if not self.path.exists():
                return []
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise JournalError(f"cannot read journal {self.path}: {exc}") from exc

        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JournalError(
                    f"corrupt journal {self.path} at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise JournalError(
                    f"corrupt journal {self.path} at line {line_number}: expected object"
                )
            records.append(record)
        return records if limit is None else records[-limit:]
