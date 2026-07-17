"""Explicit event chain of a replay run.

Every simulated trade unfolds as JSONL events in the run directory:

    candidate            the strategy emitted a signal (levels + ex ante features)
    portfolio_decision   accepted / rejected, with the reason and the caps state
    order                the simulated order (policy, qty, notional, risk metrics)
    fill                 entry fill (price, time, price_source_time, slippage)
    exit                 exit fill (reason, collision flag, fees, every R metric)

All policies consume the SAME candidate stream: a rejection is a decision
event, never a silently missing candidate.
"""

from __future__ import annotations

import json
from pathlib import Path


class EventLog:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("w")

    def emit(self, kind: str, **payload) -> dict:
        record = {"event": kind, **payload}
        self._fh.write(json.dumps(record, default=str) + "\n")
        return record

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
