"""Separate updater for the COT gate cache (state/cot_gate.json).

This is the ONLY place the COT network fetch happens. It is meant to run on a
slow schedule (daily launchd/cron is plenty — COT is weekly), completely
outside the trading loop. The gold_cot strategy then reads the cache offline
via orum.strategies.cot_gate, so a slow/failed COT fetch can never stall the
per-loop hot path and never affects BTC or ETH.

On a fetch failure the existing cache is LEFT UNTOUCHED (we never overwrite a
good gate with a guess); if it then goes stale, the reader downgrades gold to
no-trade on its own. Never writes a fabricated fallback.

Usage:
  uv run python scripts/update_cot_gate.py            # write state/cot_gate.json
  uv run python scripts/update_cot_gate.py --print    # also echo the gate
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import cot_index, fetch_cot  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orum.paths import COT_GATE_PATH  # noqa: E402

MARKET = "GOLD - COMMODITY EXCHANGE"
LOOKBACK = 156
THRESHOLD = 20.0


def compute_gate() -> dict:
    cot = fetch_cot(range(2006, datetime.now(timezone.utc).year + 1), MARKET)
    if not cot:
        raise RuntimeError("fetch_cot returned no rows")
    idx = cot_index([r["comm_net"] for r in cot], LOOKBACK)
    last, value = cot[-1], round(idx[-1], 1)
    return {
        "report_date": last["report_date"],
        "usable_from": last["usable_from"],
        "cot_index": value,
        "gate_on": value <= THRESHOLD,
        "threshold": THRESHOLD,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_gate(gate: dict, path=COT_GATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cot_gate.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(gate, f, indent=2)
        os.replace(tmp, path)  # atomic: the reader never sees a half-written file
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    try:
        gate = compute_gate()
    except Exception as exc:  # noqa: BLE001 - a failed fetch must leave the old cache intact
        print(f"cot gate update FAILED (cache left untouched): {exc}", file=sys.stderr)
        return 1
    write_gate(gate)
    print(f"cot gate written -> {COT_GATE_PATH}  "
          f"(index {gate['cot_index']} {'<=' if gate['gate_on'] else '>'} {THRESHOLD} "
          f"=> gate_{'ON' if gate['gate_on'] else 'OFF'}, report {gate['report_date']})")
    if "--print" in sys.argv:
        print(json.dumps(gate, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
