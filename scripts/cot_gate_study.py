"""Phase 2 — COT gate study on gold (PLAN_BACKTESTS.md rang 4, tv_catalogue #3).

Question: do COT positioning extremes predict gold forward returns at 1-8 week
horizons — i.e. is the COT index a usable "période propice" detector?

Design (study, not a strategy):
  * Weekly legacy COT (GC, COMEX) 2006->, Briese COT-index (%-rank, 156w) on
    commercial, non-commercial and non-reportable net positions.
  * Prices = GC=F daily closes. Each report is joined at `usable_from`
    (report Tuesday + 3d = release Friday) — entry price is the first close
    ON/AFTER that date. No lookahead.
  * Forward returns over 5/10/20/40 trading days per COT-index bucket, vs the
    unconditional baseline. Divergence variant: commercials at one extreme AND
    non-reportables (retail) at the opposite one.
  * Split pre/post 2016 to check stability. ⚠ horizons > 5d overlap week to
    week -> t-stats are optimistic; treat as ranking evidence, not gospel.

Usage: uv run python scripts/cot_gate_study.py
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import fetch_cot, fetch_daily, cot_index

HORIZONS = [5, 10, 20, 40]          # trading days
LOOKBACK = 156                      # 3y percentile window
LO, HI = 20.0, 80.0                 # extreme thresholds


def stats(rets: list[float]) -> str:
    if not rets:
        return "        (n=0)"
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / max(1, n - 1)
    t = mean / (math.sqrt(var / n) + 1e-12)
    hit = 100.0 * sum(1 for r in rets if r > 0) / n
    return f"mean {mean*100:+6.2f}%  hit {hit:5.1f}%  t {t:+5.2f}  (n={n})"


def main() -> None:
    cot = fetch_cot(range(2006, 2027), "GOLD - COMMODITY EXCHANGE")
    gc = fetch_daily("GC=F", "2005-01-01")
    closes = [b["close"] for b in gc]
    dates = [datetime.fromtimestamp(b["time"], tz=timezone.utc).strftime("%Y-%m-%d")
             for b in gc]

    idx_comm = cot_index([r["comm_net"] for r in cot], LOOKBACK)
    idx_noncomm = cot_index([r["noncomm_net"] for r in cot], LOOKBACK)
    idx_nonrep = cot_index([r["nonrep_net"] for r in cot], LOOKBACK)

    # join: for each weekly report, first daily bar index ON/AFTER usable_from
    joined = []            # (bar_i, icomm, inoncomm, inonrep, year)
    j = 0
    for k, r in enumerate(cot):
        if k < LOOKBACK:   # warm-up: percentile not yet meaningful
            continue
        uf = r["usable_from"]
        while j < len(dates) and dates[j] < uf:
            j += 1
        if j >= len(dates):
            break
        joined.append((j, idx_comm[k], idx_noncomm[k], idx_nonrep[k], int(uf[:4])))

    def fwd(bar_i: int, h: int) -> float | None:
        if bar_i + h >= len(closes):
            return None
        return closes[bar_i + h] / closes[bar_i] - 1.0

    buckets = {
        "baseline (all)":            lambda c, nc, nr: True,
        "comm HIGH (>=80)":          lambda c, nc, nr: c >= HI,
        "comm LOW  (<=20)":          lambda c, nc, nr: c <= LO,
        "noncomm HIGH (>=80)":       lambda c, nc, nr: nc >= HI,
        "noncomm LOW  (<=20)":       lambda c, nc, nr: nc <= LO,
        "divergence LONG  (comm>=80 & nonrep<=20)":  lambda c, nc, nr: c >= HI and nr <= LO,
        "divergence SHORT (comm<=20 & nonrep>=80)":  lambda c, nc, nr: c <= LO and nr >= HI,
    }

    print("=" * 100)
    print(f"COT GATE STUDY — gold GC, {len(joined)} usable weeks "
          f"({cot[LOOKBACK]['usable_from']} -> {cot[-1]['usable_from']}), "
          f"COT-index lookback {LOOKBACK}w, extremes <={LO:.0f} / >={HI:.0f}")
    print("  join on usable_from (report+3d) — no lookahead. "
          "Horizons >5d overlap: t optimistic.")
    print("=" * 100)

    for era, cond in [("FULL 2009->2026", lambda y: True),
                      ("PRE  2016", lambda y: y < 2016),
                      ("POST 2016", lambda y: y >= 2016)]:
        print(f"\n--- {era} ---")
        for name, f in buckets.items():
            line = f"  {name:44s}"
            for h in HORIZONS:
                rets = [x for (i, c, nc, nr, y) in joined
                        if cond(y) and f(c, nc, nr) and (x := fwd(i, h)) is not None]
                line += f" | {h:2d}d {stats(rets)}"
            print(line)

    print("\nRead: a gate is useful if its mean/hit CLEARLY beats baseline at the "
          "same horizon, in BOTH eras, with n large enough to matter.")


if __name__ == "__main__":
    main()
