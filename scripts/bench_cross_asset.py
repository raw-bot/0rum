"""Phase 1 — cross-asset daily reference benchmarks (PLAN_BACKTESTS.md).

NOT strategies to deploy — reference points. Per the objective function
(2026-07-04): we do NOT need to beat buy & hold; these tables tell us what
"simple trend, coûts inclus" yields per asset so any candidate gate/brain has
an honest yardstick (Donchian = the complexity bar).

Systems (daily bars, long-only, next-open execution, 0.1% round-trip):
  bh          buy & hold (reference only)
  donchian    enter close > 20d high, exit close < 10d low (Turtle-ish)
  supertrend  ATR(10) x 3.0 flip (Boring Edge config)

Assets: GC=F (gold), BTC, ETH, PAXG (Binance daily).

Usage: uv run python scripts/bench_cross_asset.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_layer import fetch_daily, fetch_klines

FEE_RT = 0.001          # 0.1% round trip
ENTRY_N, EXIT_M = 20, 10
ST_LEN, ST_MULT = 10, 3.0


def atr(h, l, c, n):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a, out = 1 / n, [tr[0]]
    for x in tr[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def metrics(equity: list[float], exposure: float, trades: int, label: str,
            n_days: int) -> str:
    yrs = max(1e-9, n_days / 365.25)
    total = equity[-1] / equity[0]
    cagr = total ** (1 / yrs) - 1
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    return (f"  {label:10s}: x{total:7.2f} | CAGR {cagr*100:+6.1f}% | maxDD {mdd*100:5.1f}% "
            f"| expo {exposure*100:5.1f}% | trades {trades}")


def run(bars: list[dict], name: str) -> None:
    o = [b["open"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]
    n = len(bars)
    f = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"\n== {name} == {f(bars[0]['time'])} -> {f(bars[-1]['time'])} ({n} days)")

    # buy & hold
    print(metrics([c[0]] + c, 1.0, 1, "bh", n))

    # donchian 20/10 long-only — signal bar EXCLUDED from its own channel
    eq, pos, entry_px, trades, in_days = [1.0], 0, 0.0, 0, 0
    for i in range(ENTRY_N + 2, n):
        hi20 = max(c[i - 1 - ENTRY_N:i - 1])
        lo10 = min(c[i - 1 - EXIT_M:i - 1])
        if pos == 0 and c[i - 1] > hi20 and i < n:
            pos, entry_px, trades = 1, o[i] * (1 + FEE_RT / 2), trades + 1
        elif pos == 1 and c[i - 1] < lo10:
            eq.append(eq[-1] * (o[i] * (1 - FEE_RT / 2)) / entry_px)
            pos = 0
        if pos == 1:
            in_days += 1
    if pos == 1:
        eq.append(eq[-1] * (c[-1] * (1 - FEE_RT / 2)) / entry_px)
    print(metrics(eq, in_days / n, trades, "donchian", n))

    # supertrend 10/3 long-only
    a = atr(h, l, c, ST_LEN)
    st_dir, ub, lb = 1, 0.0, 0.0
    eq, pos, entry_px, trades, in_days = [1.0], 0, 0.0, 0, 0
    for i in range(1, n):
        mid = (h[i] + l[i]) / 2
        nub, nlb = mid + ST_MULT * a[i], mid - ST_MULT * a[i]
        ub = min(nub, ub) if c[i - 1] <= ub else nub
        lb = max(nlb, lb) if c[i - 1] >= lb else nlb
        prev = st_dir
        st_dir = 1 if c[i] > ub else (-1 if c[i] < lb else st_dir)
        if i + 1 >= n:
            break
        if prev != 1 and st_dir == 1 and pos == 0:
            pos, entry_px, trades = 1, o[i + 1] * (1 + FEE_RT / 2), trades + 1
        elif prev == 1 and st_dir != 1 and pos == 1:
            eq.append(eq[-1] * (o[i + 1] * (1 - FEE_RT / 2)) / entry_px)
            pos = 0
        if pos == 1:
            in_days += 1
    if pos == 1:
        eq.append(eq[-1] * (c[-1] * (1 - FEE_RT / 2)) / entry_px)
    print(metrics(eq, in_days / n, trades, "supertrend", n))


def main() -> None:
    run(fetch_daily("GC=F", "2005-01-01"), "GOLD (GC=F)")
    run(fetch_klines("BTCUSDT", "1d", 3200), "BTC (Binance 1d)")
    run(fetch_klines("ETHUSDT", "1d", 3200), "ETH (Binance 1d)")
    run(fetch_klines("PAXGUSDT", "1d", 2500), "PAXG (Binance 1d)")


if __name__ == "__main__":
    main()
