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

import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import cot_index, fetch_cot, CACHE_DIR  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orum.paths import COT_GATE_PATH
from orum.fsio import atomic_write_json
from orum.strategies.cot_calendar import publication_at  # noqa: E402

MARKET = "GOLD - COMMODITY EXCHANGE"
LOOKBACK = 156
THRESHOLD = 20.0


def compute_gate() -> dict:
    now = datetime.now(timezone.utc)
    year = now.year
    # Reuse the long-history cache already present. Bootstrap only three
    # completed years if absent; refresh the current year independently.
    key = MARKET.lower().replace(" ", "_").replace("-", "")[:24]
    legacy = os.path.join(CACHE_DIR, f"cot_{key}_2006_{year}.csv")
    if os.path.exists(legacy):
        with open(legacy) as handle:
            history = list(csv.DictReader(handle))
        history = [row for row in history if int(row["report_date"][:4]) < year]
    else:
        history = fetch_cot(range(year - 3, year), MARKET)
    refresh_path = COT_GATE_PATH.with_name("cot_current_year.json")
    current = None
    try:
        cached = json.loads(refresh_path.read_text())
        fetched = datetime.fromisoformat(cached["fetched_at"])
        if cached["year"] == year and timedelta(0) <= now - fetched < timedelta(hours=6):
            current = cached["rows"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if current is None:
        current = fetch_cot(range(year, year + 1), MARKET, cache=False)
        if not current:
            raise RuntimeError("current-year COT refresh failed")
        atomic_write_json(refresh_path, {"year": year, "fetched_at": now.isoformat(), "rows": current})
    current = [row for row in current if publication_at(row["report_date"]) <= now]
    combined = {row["report_date"]: row for row in history + current}
    cot = [combined[key] for key in sorted(combined)]
    if len(cot) < LOOKBACK:
        raise RuntimeError("insufficient COT history")
    last = cot[-1]
    report = datetime.fromisoformat(last["report_date"]).replace(tzinfo=timezone.utc)
    if not timedelta(0) <= now - report <= timedelta(days=10):
        raise RuntimeError(f"stale economic COT report {last['report_date']}")
    value = round(cot_index([float(row["comm_net"]) for row in cot], LOOKBACK)[-1], 1)
    published = publication_at(last["report_date"])
    return {"report_date": last["report_date"], "usable_from": published.date().isoformat(),
            "published_at": published.isoformat(), "cot_index": value,
            "gate_on": value <= THRESHOLD, "threshold": THRESHOLD,
            "updated_at": now.isoformat(timespec="seconds"), "calendar_version": "cftc_2026_20260905"}


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
        atomic_write_json(COT_GATE_PATH.with_name("cot_refresh_status.json"), {"status": "error", "ts": datetime.now(timezone.utc).isoformat(), "error": str(exc)})
        return 1
    write_gate(gate)
    atomic_write_json(COT_GATE_PATH.with_name("cot_refresh_status.json"), {"status": "ok", "ts": gate["updated_at"], "report_date": gate["report_date"]})
    print(f"cot gate written -> {COT_GATE_PATH}  "
          f"(index {gate['cot_index']} {'<=' if gate['gate_on'] else '>'} {THRESHOLD} "
          f"=> gate_{'ON' if gate['gate_on'] else 'OFF'}, report {gate['report_date']})")
    if "--print" in sys.argv:
        print(json.dumps(gate, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
