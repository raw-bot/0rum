"""Phase 2 — COT gate APPLIED: gold daily trend, gated by positioning extremes.

The study (cot_gate_study.py) says: on gold, spec-side extremes CONTINUE —
comm COT-index <= 20 (commercials max short = specs max long) and
noncomm >= 80 both precede ABOVE-baseline forward returns at 5-40d, in both
eras. So the gate here is CONTINUATION (trade WITH the extreme), the exact
inverse of the nolan.vader video's contrarian pitch.

This script turns that into the plan's central question: does gating a simple
long-only gold trend system on those windows IMPROVE conditional expectancy
(PF per active window, exposure down, DD down)?

Variants (same Donchian 20/10 + Supertrend 10/3 engines as bench_cross_asset):
  ungated        all signals taken (= bench reference)
  gate_comm      entries allowed only while comm COT-index <= 20
  gate_noncomm   entries allowed only while noncomm COT-index >= 80
  gate_either    either condition
Gate affects ENTRIES only; an open position exits on its own rules (the gate
decides when to start playing, not when to stop — période propice semantics).
Gate value switches at each report's usable_from date (no lookahead).

Usage: uv run python scripts/cot_gated_gold.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import fetch_cot, fetch_daily, cot_index

FEE_RT = 0.001
ENTRY_N, EXIT_M = 20, 10
ST_LEN, ST_MULT = 10, 3.0
LOOKBACK, LO, HI = 156, 20.0, 80.0


def atr(h, l, c, n):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a, out = 1 / n, [tr[0]]
    for x in tr[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def build_gates(bars: list[dict]) -> dict[str, list[bool]]:
    cot = fetch_cot(range(2006, 2027), "GOLD - COMMODITY EXCHANGE")
    ic = cot_index([r["comm_net"] for r in cot], LOOKBACK)
    inc = cot_index([r["noncomm_net"] for r in cot], LOOKBACK)
    dates = [datetime.fromtimestamp(b["time"], tz=timezone.utc).strftime("%Y-%m-%d")
             for b in bars]
    n = len(bars)
    g_comm, g_noncomm = [False] * n, [False] * n
    k = 0
    cur_c, cur_nc = False, False
    for i, d in enumerate(dates):
        while k < len(cot) and cot[k]["usable_from"] <= d:
            if k >= LOOKBACK:
                cur_c = ic[k] <= LO
                cur_nc = inc[k] >= HI
            k += 1
        g_comm[i], g_noncomm[i] = cur_c, cur_nc
    return {"ungated": [True] * n, "gate_comm": g_comm, "gate_noncomm": g_noncomm,
            "gate_either": [a or b for a, b in zip(g_comm, g_noncomm)]}


def report(label, eq, n_days, in_days, trades, wins, gate_days):
    yrs = n_days / 365.25
    total = eq[-1]
    cagr = total ** (1 / yrs) - 1
    peak, mdd = 1.0, 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    wr = 100 * wins / trades if trades else 0.0
    print(f"  {label:12s}: x{total:6.2f} | CAGR {cagr*100:+6.1f}% | maxDD {mdd*100:5.1f}% "
          f"| expo {100*in_days/n_days:5.1f}% | gate-on {100*gate_days/n_days:5.1f}% "
          f"| trades {trades:3d} | win {wr:4.1f}%")


def donchian(bars, gate):
    o = [b["open"] for b in bars]; c = [b["close"] for b in bars]
    n = len(bars)
    eq, pos, px, tr, wins, in_d = [1.0], 0, 0.0, 0, 0, 0
    for i in range(ENTRY_N + 2, n):
        hi = max(c[i - 1 - ENTRY_N:i - 1]); lo = min(c[i - 1 - EXIT_M:i - 1])
        if pos == 0 and gate[i - 1] and c[i - 1] > hi:
            pos, px, tr = 1, o[i] * (1 + FEE_RT / 2), tr + 1
        elif pos == 1 and c[i - 1] < lo:
            x = o[i] * (1 - FEE_RT / 2)
            wins += x > px
            eq.append(eq[-1] * x / px); pos = 0
        in_d += pos
    if pos:
        x = c[-1] * (1 - FEE_RT / 2); wins += x > px; eq.append(eq[-1] * x / px)
    return eq, in_d, tr, wins


def supertrend(bars, gate):
    o = [b["open"] for b in bars]; h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]; c = [b["close"] for b in bars]
    n = len(bars); a = atr(h, l, c, ST_LEN)
    d, ub, lb = 1, 0.0, 0.0
    eq, pos, px, tr, wins, in_d = [1.0], 0, 0.0, 0, 0, 0
    for i in range(1, n):
        mid = (h[i] + l[i]) / 2
        nub, nlb = mid + ST_MULT * a[i], mid - ST_MULT * a[i]
        ub = min(nub, ub) if c[i - 1] <= ub else nub
        lb = max(nlb, lb) if c[i - 1] >= lb else nlb
        prev = d
        d = 1 if c[i] > ub else (-1 if c[i] < lb else d)
        if i + 1 >= n:
            break
        if prev != 1 and d == 1 and pos == 0 and gate[i]:
            pos, px, tr = 1, o[i + 1] * (1 + FEE_RT / 2), tr + 1
        elif prev == 1 and d != 1 and pos == 1:
            x = o[i + 1] * (1 - FEE_RT / 2)
            wins += x > px
            eq.append(eq[-1] * x / px); pos = 0
        in_d += pos
    if pos:
        x = c[-1] * (1 - FEE_RT / 2); wins += x > px; eq.append(eq[-1] * x / px)
    return eq, in_d, tr, wins


def hold_while_gate(bars, gate):
    """The study's literal translation: long from gate-on to gate-off, no other
    signal. This is what cot_gate_study.py actually measured."""
    o = [b["open"] for b in bars]; c = [b["close"] for b in bars]
    n = len(bars)
    eq, pos, px, tr, wins, in_d = [1.0], 0, 0.0, 0, 0, 0
    for i in range(1, n):
        if pos == 0 and gate[i - 1]:
            pos, px, tr = 1, o[i] * (1 + FEE_RT / 2), tr + 1
        elif pos == 1 and not gate[i - 1]:
            x = o[i] * (1 - FEE_RT / 2)
            wins += x > px
            eq.append(eq[-1] * x / px); pos = 0
        in_d += pos
    if pos:
        x = c[-1] * (1 - FEE_RT / 2); wins += x > px; eq.append(eq[-1] * x / px)
    return eq, in_d, tr, wins


def main():
    bars = fetch_daily("GC=F", "2005-01-01")
    n = len(bars)
    gates = build_gates(bars)
    f = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
    print("=" * 100)
    print(f"COT-GATED GOLD TREND — GC=F daily {f(bars[0]['time'])} -> {f(bars[-1]['time'])}, "
          f"fees {FEE_RT*100:.1f}% RT, gate on ENTRIES only")
    print("=" * 100)
    for eng_name, eng in [("DONCHIAN 20/10", donchian), ("SUPERTREND 10/3", supertrend),
                          ("HOLD-WHILE-GATE (étude littérale)", hold_while_gate)]:
        print(f"\n-- {eng_name} --")
        for gname, g in gates.items():
            eq, in_d, tr, wins = eng(bars, g)
            report(gname, eq, n, in_d, tr, wins, sum(g))


if __name__ == "__main__":
    main()
