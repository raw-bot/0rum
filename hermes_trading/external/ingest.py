"""Phase 2: local ingestion of parsed external signals.

Well-formed signals are appended to ``state/external_signals.jsonl``; an
in-memory dedup index (set of dedup hashes) makes a signal whose identity was
already seen land as ``DUPLICATE`` and never enter the pipeline twice. The
index is rebuilt from the file on construction, so dedup survives a restart.

Malformed payloads never reach the signals file: they are recorded as
incidents in ``events.jsonl`` only, keeping the signals log strictly typed.

Nothing here validates business rules (allowlist, risk, position) or places an
order — that is Phase 3+. The store is dependency-injected (path + logger) so
it is testable in isolation and never touches global state implicitly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from hermes_trading.events import log_event
from hermes_trading.external.signal import (
    ExternalSignal,
    ExternalSignalError,
    ExternalSignalStatus,
    parse_external_signal,
)
from hermes_trading.paths import STATE_DIR

# Declared here (not in paths.py) so Phase 2 leaves every native file untouched.
EXTERNAL_SIGNALS_PATH = STATE_DIR / "external_signals.jsonl"

Logger = Callable[..., dict]


@dataclass(frozen=True)
class IngestResult:
    """Outcome of ingesting one payload/signal.

    ``RECEIVED``  — new identity, written and admitted to the pipeline.
    ``DUPLICATE`` — identity already seen; written for audit, not re-admitted.
    ``REJECTED``  — payload was malformed (structural); ``errors`` says why.
    """

    status: ExternalSignalStatus
    signal: ExternalSignal | None
    dedup_hash: str | None
    errors: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.status is ExternalSignalStatus.RECEIVED


class ExternalSignalStore:
    def __init__(self, *, path: Path | str | None = None, logger: Logger = log_event):
        self.path = Path(path) if path is not None else EXTERNAL_SIGNALS_PATH
        self._log = logger
        self._seen: set[str] = self._load_index()

    # -- persistence -------------------------------------------------------
    def _load_index(self) -> set[str]:
        seen: set[str] = set()
        if not self.path.exists():
            return seen
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a torn line must not crash recovery
            digest = record.get("dedup_hash")
            if digest:
                seen.add(digest)
        return seen

    def _append(self, signal: ExternalSignal, status: ExternalSignalStatus) -> None:
        record = {
            **signal.to_record(),
            "status": status.value,
            "ingested_at": datetime.now(UTC).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    # -- ingestion ---------------------------------------------------------
    def ingest(self, signal: ExternalSignal) -> IngestResult:
        """Persist + dedup an already-parsed signal."""
        digest = signal.dedup_hash()
        is_dup = digest in self._seen
        status = ExternalSignalStatus.DUPLICATE if is_dup else ExternalSignalStatus.RECEIVED
        self._append(signal, status)
        detail = f"{signal.event} {signal.symbol} {signal.timeframe} bar={signal.bar_time}"
        if is_dup:
            self._log(
                "external_signal_duplicate",
                f"dropped duplicate {detail}",
                source=signal.source,
                strategy=signal.strategy,
                dedup_hash=digest,
            )
        else:
            self._seen.add(digest)
            self._log(
                "external_signal_received",
                detail,
                source=signal.source,
                strategy=signal.strategy,
                dedup_hash=digest,
            )
        return IngestResult(status=status, signal=signal, dedup_hash=digest)

    def submit(self, payload: dict | str) -> IngestResult:
        """Parse a raw payload then ingest it. Malformed → REJECTED (logged)."""
        try:
            signal = parse_external_signal(payload)
        except ExternalSignalError as exc:
            self._log("external_signal_malformed", "; ".join(exc.errors))
            return IngestResult(
                status=ExternalSignalStatus.REJECTED,
                signal=None,
                dedup_hash=None,
                errors=tuple(exc.errors),
            )
        return self.ingest(signal)
