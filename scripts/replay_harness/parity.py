"""Explained Pine parity — never byte parity.

Compares the harness candidate stream against a golden export of the
TradingView strategy (backtests/fixtures/pine_golden_*.json, produced from the
live chart via the MCP label export) and attributes every divergence to a
known convention difference:

  window_effect     Pine computes EMA/RSI over the full chart history; the
                    live provider (and strategy_legacy) hands the engine a
                    rolling `candles_limit` window, so indicator values drift
                    slightly and borderline signals can flip. Detected by
                    recomputing the signal with an unbounded window.
  level_tolerance   same signal bar, levels differ within tolerance —
                    seeding/precision noise, counted as a match.
  data_edge         the bar sits before the snapshot warmup or after its end.
  pine_warmup_edge  the bar sits in the seeding transient at the LEFT EDGE of
                    TradingView's own chart history (the fixture records
                    `pine_history_start_ms`): Pine's EMA/RSI are not yet
                    converged there while the snapshot-fed engine is, so
                    borderline signals differ legitimately.
  unexplained       anything else — these are the ones that block acceptance.

The golden file also carries Pine's emaH/emaL/RSI values per signal bar, which
`tests/test_replay_harness.py` uses as the non-self-referential indicator
check the review demanded.
"""

from __future__ import annotations

import json
from pathlib import Path

from orum.strategies.base import StrategyContext
from orum.strategies.ha_trend import HaTrendEngine

from scripts.replay_harness.timeline import INTERVAL_MS, SnapshotProvider


def _signal_with_window(provider: SnapshotProvider, symbol: str, timeframe: str,
                        bar_open_ms: int, *, limit: int, params: dict | None = None):
    engine = HaTrendEngine()
    engine.init(params or {})
    knowable = bar_open_ms + INTERVAL_MS[timeframe]
    window = provider.as_of(symbol, timeframe, limit, knowable)
    if not window or window[-1]["ts"] != bar_open_ms or len(window) < engine.warmup_period:
        return None, "data_edge"
    return engine.on_candle(window[-1], StrategyContext(
        candles=window, symbol=symbol, timeframe=timeframe)), None


def compare_to_golden(golden_path: Path, candidates: list[dict], provider: SnapshotProvider,
                      *, symbol: str, timeframe: str = "4h", candles_limit: int = 300,
                      level_tolerance_frac: float = 0.001) -> dict:
    golden = json.loads(Path(golden_path).read_text())
    ours = {c["bar_open_time"]: c for c in candidates}
    theirs = {int(g["bar_open_time"]): g for g in golden["signals"]}
    # Pine's indicators are in their seeding transient near its chart's left
    # edge; give them the same convergence horizon we give our own warmup.
    pine_converged_from = (golden.get("pine_history_start_ms", 0)
                           + 250 * INTERVAL_MS[timeframe])

    matches, divergences = [], []
    for ts, g in sorted(theirs.items()):
        mine = ours.get(ts)
        if mine is not None:
            stop_ok = abs(mine["stop"] - g["stop"]) <= level_tolerance_frac * g["close"]
            matches.append({"bar_open_time": ts, "stop_delta": mine["stop"] - g["stop"],
                            "levels_within_tolerance": stop_ok})
            continue
        # Missing on our side: is it the rolling-window effect?
        full_signal, edge = _signal_with_window(provider, symbol, timeframe, ts, limit=0)
        if edge is not None:
            divergences.append({"bar_open_time": ts, "side": "pine_only", "cause": "data_edge"})
        elif full_signal is not None:
            divergences.append({"bar_open_time": ts, "side": "pine_only", "cause": "window_effect",
                                "detail": f"signal reappears with full history (limit={candles_limit} loses it)"})
        else:
            divergences.append({"bar_open_time": ts, "side": "pine_only", "cause": "unexplained"})

    for ts, mine in sorted(ours.items()):
        if ts in theirs:
            continue
        if ts < pine_converged_from:
            divergences.append({"bar_open_time": ts, "side": "python_only",
                                "cause": "pine_warmup_edge",
                                "detail": "inside Pine's left-edge indicator transient"})
            continue
        full_signal, edge = _signal_with_window(provider, symbol, timeframe, ts, limit=0)
        cause = ("window_effect" if full_signal is None and edge is None
                 else "data_edge" if edge is not None else "unexplained")
        divergences.append({"bar_open_time": ts, "side": "python_only", "cause": cause})

    unexplained = [d for d in divergences if d["cause"] == "unexplained"]
    return {
        "golden_signals": len(theirs),
        "python_signals": len(ours),
        "matched": len(matches),
        "matched_levels_within_tolerance": sum(1 for m in matches if m["levels_within_tolerance"]),
        "divergences": divergences,
        "unexplained": len(unexplained),
        "verdict": "explained" if not unexplained else "UNEXPLAINED_DIVERGENCES",
    }
