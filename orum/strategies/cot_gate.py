"""Isolated reader for the COT gate cache (`state/cot_gate.json`).

COT positioning data is a WEEKLY, gold-specific dependency. It must never be
fetched inside a strategy's `on_candle` (network in the hot path) and must
never leak into the BTC/ETH strategies. So the flow is split in two:

  * a SEPARATE updater (`scripts/update_cot_gate.py`) fetches COT, computes the
    gate, and writes the cache file — this is where the network call lives;
  * this module READS and VALIDATES that cache, offline, with no dependency on
    the COT data layer at all.

`read_gate` is deliberately total: it NEVER raises and NEVER invents a value.
Any problem — file missing, bad JSON, missing/!bool `gate_on`, non-numeric
`cot_index`, missing `report_date`, or a stale `updated_at` — returns `None`,
which the gold_cot strategy treats as "no-trade". A missing gate can therefore
only ever stop gold from trading; it can never fabricate a signal, and it can
never touch BTC or ETH (which do not import this module).

Cache schema (all keys required):
    {
      "report_date": "2026-06-30",     # str, the COT report Tuesday
      "usable_from": "2026-07-03",     # str, release date (report + 3d)
      "cot_index":   53.9,             # number or null
      "gate_on":     false,            # bool: cot_index <= threshold
      "threshold":   20.0,             # number, for auditability
      "updated_at":  "2026-07-08T08:00:00+00:00"   # ISO8601, freshness anchor
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# COT reports are weekly; a daily updater refreshes the file. Anything older
# than this many days means the updater has been down long enough that we no
# longer trust the gate -> no-trade rather than trade on stale positioning.
DEFAULT_MAX_AGE_DAYS = 10.0


@dataclass(frozen=True)
class CotGate:
    gate_on: bool
    cot_index: float | None
    report_date: str
    usable_from: str | None
    threshold: float | None
    updated_at: datetime


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_gate(
    path: str | Path,
    *,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> CotGate | None:
    """Return the validated gate, or None if the cache is absent, malformed,
    or stale. Never raises; never fabricates a fallback."""
    p = Path(path)
    try:
        raw = p.read_text()
    except OSError:
        return None  # file missing / unreadable

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None  # not valid JSON
    if not isinstance(data, dict):
        return None

    report_date = data.get("report_date")
    if not isinstance(report_date, str) or not report_date:
        return None  # report_date is mandatory

    gate_on = data.get("gate_on")
    if not isinstance(gate_on, bool):  # rejects ints/None/str, keeps intent explicit
        return None

    cot_index = data.get("cot_index")
    if cot_index is not None and (isinstance(cot_index, bool) or not isinstance(cot_index, (int, float))):
        return None  # must be a real number, or explicitly null

    updated_at = _parse_iso(data.get("updated_at"))
    if updated_at is None:
        return None  # no trustworthy freshness anchor -> treat as stale

    reference = now or datetime.now(timezone.utc)
    if reference - updated_at > _timedelta_days(max_age_days):
        return None  # stale: updater has not refreshed the gate recently enough

    threshold = data.get("threshold")
    threshold = float(threshold) if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) else None

    return CotGate(
        gate_on=gate_on,
        cot_index=float(cot_index) if cot_index is not None else None,
        report_date=report_date,
        usable_from=data.get("usable_from") if isinstance(data.get("usable_from"), str) else None,
        threshold=threshold,
        updated_at=updated_at,
    )


def _timedelta_days(days: float):
    from datetime import timedelta

    return timedelta(days=days)
