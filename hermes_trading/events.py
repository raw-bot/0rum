"""Persistent structured event log.

Heartbeats are overwritten every iteration, so incidents (price source
flips, guardrail transitions, quarantines, worker failures) used to leave no
trace. Events are appended to state/events.jsonl; logging must never break
the worker."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from hermes_trading.paths import EVENTS_PATH


def log_event(kind: str, detail: str, **fields) -> dict:
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "kind": kind,
        "detail": detail,
        **fields,
    }
    try:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass
    print(f"[{kind}] {detail}", flush=True)
    return record
