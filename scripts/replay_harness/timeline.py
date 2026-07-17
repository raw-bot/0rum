"""Simulated clock and lookahead-proof data access.

The Binance kline timestamp is the bar OPEN. A bar becomes knowable only at
`ts + interval` — `SnapshotProvider.as_of` is the single place that rule is
enforced, mirroring the live filter in scripts/run_paper_portfolio.py
(`row["ts"] + interval_ms <= now_ms`). No harness component may touch snapshot
rows directly.

Every simulated trade carries six explicit timestamps (all epoch ms):
  bar_open_time           open of the signal bar (the raw Binance ts)
  bar_close_time          bar_open_time + interval — earliest knowable moment
  decision_available_time bar_close_time (signal exists only on a closed bar)
  cycle_observed_time     the simulated worker cycle that first saw the bar
  fill_time               when the simulated order fills
  price_source_time       timestamp of the bar whose price was used as fill
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from scripts.replay_harness.snapshot import INTERVAL_MS, load_series

CYCLE_MS = 900_000  # the live launchd worker runs every 15 minutes


@dataclass
class SnapshotProvider:
    """Read-only, as_of-sliced access to snapshot series. Also records every
    (symbol, interval) it served so the run report can fingerprint its data."""

    series: dict[tuple[str, str], list[dict]] = field(default_factory=dict)
    served: set = field(default_factory=set)
    _ts_index: dict[tuple[str, str], list[int]] = field(default_factory=dict)

    def load(self, symbol: str, interval: str) -> None:
        self.series[(symbol, interval)] = load_series(symbol, interval)

    def as_of(self, symbol: str, interval: str, limit: int, now_ms: int) -> list[dict]:
        """The last `limit` bars CLOSED at `now_ms` (bar close <= now).
        Bisect over the sorted open timestamps — semantics identical to the
        linear filter `row["ts"] + step <= now_ms`, just O(log n)."""
        rows = self.series.get((symbol, interval))
        if rows is None:
            raise KeyError(f"snapshot has no series for {symbol} {interval}")
        self.served.add((symbol, interval))
        key = (symbol, interval)
        ts_list = self._ts_index.get(key)
        if ts_list is None or len(ts_list) != len(rows):
            ts_list = [row["ts"] for row in rows]
            assert all(a < b for a, b in zip(ts_list, ts_list[1:])), \
                f"snapshot {key} is not strictly time-ordered"
            self._ts_index[key] = ts_list
        step = INTERVAL_MS[interval]
        end = bisect_right(ts_list, now_ms - step)  # ts <= now - step  <=>  close <= now
        return rows[max(0, end - limit):end] if limit else rows[:end]

    def bound_provider(self, clock: "SimClock", limit_cap: int | None = None):
        """A `candle_provider(symbol, timeframe, limit)` closure over the
        simulated clock — exactly the signature PaperEngine expects."""
        def provider(symbol: str, timeframe: str, limit: int) -> list[dict]:
            capped = min(limit, limit_cap) if limit_cap else limit
            return self.as_of(symbol, timeframe, capped, clock.now_ms)
        return provider


@dataclass
class SimClock:
    now_ms: int = 0


def cycle_times(start_ms: int, end_ms: int, *, cycle_ms: int = CYCLE_MS, offset_ms: int = 0) -> list[int]:
    """Every simulated worker wake-up in [start, end], aligned to the cycle
    grid. `offset_ms` models the launchd phase (default: on the boundary)."""
    first = ((start_ms - offset_ms + cycle_ms - 1) // cycle_ms) * cycle_ms + offset_ms
    return list(range(first, end_ms + 1, cycle_ms))


def timestamps_for_signal(bar_open_ms: int, interval: str, *, cycle_ms: int = CYCLE_MS,
                          offset_ms: int = 0) -> dict:
    """The first four timestamps of the chain for a signal bar."""
    bar_close = bar_open_ms + INTERVAL_MS[interval]
    cycle = ((bar_close - offset_ms + cycle_ms - 1) // cycle_ms) * cycle_ms + offset_ms
    return {
        "bar_open_time": bar_open_ms,
        "bar_close_time": bar_close,
        "decision_available_time": bar_close,
        "cycle_observed_time": cycle,
    }
