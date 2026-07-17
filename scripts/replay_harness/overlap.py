"""Overlap matrix between strategies — the measurement that decides the
thesis-budget design (garder / budget partagé / répartir par régime).

Consumes ISOLATED runtime replays (one strategy per run, own account, gate
off) so that timing is never distorted by shared-account caps, and compares:

  signal proximity     signals are LONG intents (open|hold) from the cycle
                       summaries — deduped per signal candle by the engine
                       itself. Compared at three tolerances because the
                       strategies live on different clocks (AK/HA 4h,
                       UTBot 15m): same 4h bucket, within 4h, within 24h.
  exposure overlap     open->close intervals from the fills ledger; Jaccard
                       plus the directional share of A's market time also
                       covered by B (all strategies are long-only, so any
                       concurrent exposure is the SAME thesis twice).
  daily P&L corr       Pearson on same-day returns of the two equity curves.
  loss co-occurrence   P(B loses | A loses) on daily returns — drawdown
                       clustering, the diversification killer.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

H4_MS = 14_400_000


def _parse_ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def load_run(run_dir: Path) -> dict:
    summaries = json.loads((run_dir / "runtime_summaries.json").read_text())
    ledger = run_dir / "runtime_ledger"
    fills = [json.loads(l) for l in (ledger / "fills.jsonl").read_text().splitlines() if l.strip()]
    equity = [json.loads(l) for l in (ledger / "equity.jsonl").read_text().splitlines() if l.strip()]

    signals: list[int] = []
    strategy_id = None
    for s in summaries:
        for sid, intent in s["intents"].items():
            strategy_id = sid
            if intent in ("open", "hold"):
                signals.append(_parse_ts(s["ts"]))

    intervals: list[tuple[int, int]] = []
    open_ts: int | None = None
    for f in fills:
        ts = _parse_ts(f["ts"])
        if f["action"] == "open":
            open_ts = ts
        elif f["action"] == "close" and open_ts is not None:
            intervals.append((open_ts, ts))
            open_ts = None
    end_ms = _parse_ts(equity[-1]["ts"]) if equity else 0
    if open_ts is not None:
        intervals.append((open_ts, end_ms))

    daily: dict[str, float] = {}
    for row in equity:
        daily[row["ts"][:10]] = row["equity_usd"]  # last equity of the day wins
    days = sorted(daily)
    returns = {d1: daily[d1] / daily[d0] - 1.0
               for d0, d1 in zip(days, days[1:]) if daily[d0] > 0}

    return {"strategy_id": strategy_id, "signals": sorted(signals),
            "intervals": intervals, "daily_returns": returns,
            "n_fills": len(fills), "end_ms": end_ms}


def _nearest_gap(ts: int, others: list[int]) -> int | None:
    if not others:
        return None
    from bisect import bisect_left
    i = bisect_left(others, ts)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(others):
            gap = abs(others[j] - ts)
            best = gap if best is None else min(best, gap)
    return best


def signal_proximity(a: list[int], b: list[int]) -> dict:
    if not a or not b:
        return {"n_a": len(a), "n_b": len(b)}
    buckets_b = {ts // H4_MS for ts in b}
    gaps = [_nearest_gap(ts, b) for ts in a]
    within = lambda ms: sum(1 for g in gaps if g is not None and g <= ms)
    same_bucket = sum(1 for ts in a if ts // H4_MS in buckets_b)
    ordered = sorted(g for g in gaps if g is not None)
    return {
        "n_a": len(a), "n_b": len(b),
        "same_4h_bucket": same_bucket,
        "same_4h_bucket_frac": round(same_bucket / len(a), 3),
        "within_4h": within(H4_MS), "within_4h_frac": round(within(H4_MS) / len(a), 3),
        "within_24h": within(6 * H4_MS), "within_24h_frac": round(within(6 * H4_MS) / len(a), 3),
        "median_gap_hours": round(ordered[len(ordered) // 2] / 3_600_000, 1),
    }


def _total_ms(intervals: list[tuple[int, int]]) -> int:
    return sum(b - a for a, b in intervals)


def _intersection_ms(xs: list[tuple[int, int]], ys: list[tuple[int, int]]) -> int:
    total, i, j = 0, 0, 0
    while i < len(xs) and j < len(ys):
        lo = max(xs[i][0], ys[j][0])
        hi = min(xs[i][1], ys[j][1])
        if lo < hi:
            total += hi - lo
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return total


def exposure_overlap(a: list[tuple[int, int]], b: list[tuple[int, int]],
                     window_ms: int) -> dict:
    ta, tb = _total_ms(a), _total_ms(b)
    inter = _intersection_ms(sorted(a), sorted(b))
    union = ta + tb - inter
    return {
        "exposure_a_frac": round(ta / window_ms, 4) if window_ms else None,
        "exposure_b_frac": round(tb / window_ms, 4) if window_ms else None,
        "jaccard": round(inter / union, 4) if union else 0.0,
        "share_of_a_covered_by_b": round(inter / ta, 4) if ta else 0.0,
        "share_of_b_covered_by_a": round(inter / tb, 4) if tb else 0.0,
        "concurrent_days": round(inter / 86_400_000, 1),
    }


def daily_correlation(ra: dict[str, float], rb: dict[str, float]) -> dict:
    days = sorted(set(ra) & set(rb))
    xs = [ra[d] for d in days]
    ys = [rb[d] for d in days]
    n = len(days)
    if n < 30:
        return {"n_days": n}
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    pearson = cov / (vx * vy) if vx > 0 and vy > 0 else None
    # Loss clustering on days where at least one strategy actually moved.
    a_loss = {d for d in days if ra[d] < 0}
    b_loss = {d for d in days if rb[d] < 0}
    both = len(a_loss & b_loss)
    return {
        "n_days": n,
        "pearson_daily_returns": round(pearson, 3) if pearson is not None else None,
        "p_b_loses_given_a_loses": round(both / len(a_loss), 3) if a_loss else None,
        "p_a_loses_given_b_loses": round(both / len(b_loss), 3) if b_loss else None,
        "both_negative_days": both,
    }


def overlap_matrix(run_dirs: list[Path], *, start_ms: int) -> dict:
    runs = [load_run(d) for d in run_dirs]
    window_ms = max(r["end_ms"] for r in runs) - start_ms
    out = {"strategies": {r["strategy_id"]: {
        "signals": len(r["signals"]), "fills": r["n_fills"],
        "exposure_frac": round(_total_ms(r["intervals"]) / window_ms, 4),
        "final_equity_proxy": None,
    } for r in runs}, "pairs": {}}
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            a, b = runs[i], runs[j]
            key = f"{a['strategy_id']} x {b['strategy_id']}"
            out["pairs"][key] = {
                "signal_proximity_a_to_b": signal_proximity(a["signals"], b["signals"]),
                "signal_proximity_b_to_a": signal_proximity(b["signals"], a["signals"]),
                "exposure": exposure_overlap(a["intervals"], b["intervals"], window_ms),
                "pnl": daily_correlation(a["daily_returns"], b["daily_returns"]),
            }
    return out
