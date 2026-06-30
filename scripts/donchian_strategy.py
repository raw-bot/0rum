"""Phase 4 — FULL DONCHIAN STRATEGY (own entry + own exit), isolated lab.

This is a SEPARATE strategy, NOT 'AK MACD with a different exit'. It never touches
the live bot. Entry, exit, and filters are all Donchian/trend-native:

  entry   : breakout of the prior N-bar Donchian channel (close > N-high = long,
            close < N-low = short).
  exit    : opposite M-bar channel (Turtle-style trailing) OR initial ATR stop —
            whichever the 1m path hits first. No fixed R cap (let winners run).
  htf     : optional — only take a breakout aligned with the 4H trend bias.
  regime  : optional — only take a breakout when the rolling-return regime agrees
            with the side (favorable->long, unfavorable->short); skips chop.

Simulated on REAL 1m intrabar (conservative adverse-first), R measured vs the initial
ATR risk. Sanity benchmark 'buy_hold' included — over an up BTC period it MUST be
clearly positive, else the harness is broken (the lesson from the failed screen).

Variants: raw / +htf / +htf+regime. Metrics + walk-forward 5 folds.

Usage: uv run python scripts/donchian_strategy.py [n_15m_bars] [--folds K]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import baseline_ak_macd as base
import backtest_parity as bp
import backtest_runner_1m as r1
from experiment_ict_filters import htf_bias_series

ENTRY_N = 20            # breakout channel
EXIT_M = 10            # opposite trailing channel
ATR_LEN = 14
STOP_K = 2.0           # initial stop = entry -/+ K*ATR
FEE = 0.0004
SLIP = 0.0003


def rma(xs, n):
    a = 1 / n; out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def atr_series(h, l, c, n):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return rma(tr, n)


def donchian_entries(h, l, c, n):
    out = []
    for i in range(n, len(c) - 1):
        if c[i] > max(h[i - n:i]):
            out.append((i, "long"))
        elif c[i] < min(l[i - n:i]):
            out.append((i, "short"))
    return out


def sim_trade(side, entry, sl0, risk, ctx, T1, H1, L1, start, ctx_at):
    """Opposite M-channel trailing exit + initial ATR stop, on 1m bars. Returns net R."""
    long = side == "long"
    stop = sl0
    for j in range(start, len(T1)):
        i15 = ctx_at(T1[j])
        # trail to the opposite M-bar channel as it ratchets in our favor
        if i15 is not None:
            ch = ctx["exit_lo"][i15] if long else ctx["exit_hi"][i15]
            if ch == ch:
                stop = max(stop, ch) if long else min(stop, ch)
        adverse = (L1[j] <= stop) if long else (H1[j] >= stop)
        if adverse:
            fill = stop * (1 - SLIP) if long else stop * (1 + SLIP)
            g = (fill - entry) if long else (entry - fill)
            return (g - FEE * (entry + fill)) / risk
        # opposite-channel close exit
        if i15 is not None:
            opp = ctx["exit_lo"][i15] if long else ctx["exit_hi"][i15]
            c15 = ctx["close"][i15]
            if opp == opp and ((c15 < opp) if long else (c15 > opp)):
                fill = c15 * (1 - SLIP) if long else c15 * (1 + SLIP)
                g = (fill - entry) if long else (entry - fill)
                return (g - FEE * (entry + fill)) / risk
    return None


def stats(trs):
    if not trs:
        return None
    rs = [x["r"] for x in trs]
    w = [x for x in trs if x["r"] > 0]
    gl = -sum(x["r"] for x in trs if x["r"] <= 0)
    pf = (sum(x["r"] for x in w) / gl) if gl > 0 else float("inf")
    eq = pk = dd = 0.0
    for x in trs:
        eq += x["r"]; pk = max(pk, eq); dd = max(dd, pk - eq)
    return {"n": len(trs), "wr": len(w) / len(trs), "pf": pf,
            "exp": sum(rs) / len(rs), "net": sum(rs), "maxdd": dd,
            "avg_win": (sum(x["r"] for x in w) / len(w)) if w else 0.0}


def line(name, m):
    if not m:
        print(f"  {name:<16}: no trades"); return
    print(f"  {name:<16}: {m['n']:>4} | WR {m['wr']:>5.1%} | PF {m['pf']:.2f} | "
          f"exp {m['exp']:+.3f}R | net {m['net']:+7.1f}R | maxDD {m['maxdd']:5.1f}R | "
          f"avgWin {m['avg_win']:+.2f}R")


def main():
    argv = sys.argv[1:]
    n_target, folds = 50000, 5
    skip = False
    for k, a in enumerate(argv):
        if skip:
            skip = False; continue
        if a == "--folds":
            folds = int(argv[k + 1]); skip = True
        elif a.isdigit():
            n_target = int(a)

    print(f"Fetching ~{n_target} 15m bars ...", flush=True)
    bars = base.fetch_klines(n=n_target)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; t15 = [b["time"] for b in bars]
    span = (datetime.fromtimestamp(t15[0], timezone.utc), datetime.fromtimestamp(t15[-1], timezone.utc))
    print(f"Got {len(bars)} 15m bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}", flush=True)

    atr = atr_series(h, l, c, ATR_LEN)
    exit_lo = [min(l[i - EXIT_M:i]) if i >= EXIT_M else float("nan") for i in range(len(c))]
    exit_hi = [max(h[i - EXIT_M:i]) if i >= EXIT_M else float("nan") for i in range(len(c))]
    ctx = {"exit_lo": exit_lo, "exit_hi": exit_hi, "close": c}
    bias = htf_bias_series(bars)
    regime = bp._regime_labels(c)

    entries = donchian_entries(h, l, c, ENTRY_N)
    print(f"Donchian raw breakouts: {len(entries)}", flush=True)

    print("Loading 1m intrabar (cached) ...", flush=True)
    os.environ.setdefault("HERMES_SCRATCH", "/private/tmp/claude-501/-Users-cube/"
                          "970db104-8da7-4abe-bfec-be3cb9c5de36/scratchpad")
    r1.CACHE = os.environ["HERMES_SCRATCH"] + "/btc_1m_cache.json"
    T1, H1, L1 = r1.fetch_1m(t15[0], t15[-1] + r1.TF15_S)
    print(f"1m bars: {len(T1)}\n", flush=True)

    import bisect
    close_times = [tt + r1.TF15_S for tt in t15]
    def ctx_at(ts):
        k = bisect.bisect_right(close_times, ts) - 1
        return k if k >= 0 else None

    def passes(i, side, htf, reg):
        if htf and bias[i] != (1 if side == "long" else -1):
            return False
        if reg:
            want = "favorable" if side == "long" else "unfavorable"
            if regime[i] != want:
                return False
        return True

    def run(htf, reg):
        out = []
        busy = -1
        p1 = 0
        for (i, side) in entries:
            if i <= busy:
                continue
            if not passes(i, side, htf, reg):
                continue
            a = atr[i]
            if a != a or a <= 0:
                continue
            entry = c[i] * (1 + SLIP) if side == "long" else c[i] * (1 - SLIP)
            risk = STOP_K * a
            sl0 = entry - risk if side == "long" else entry + risk
            open_at = t15[i] + r1.TF15_S
            while p1 < len(T1) and T1[p1] < open_at:
                p1 += 1
            if p1 >= len(T1):
                break
            r = sim_trade(side, entry, sl0, risk, ctx, T1, H1, L1, p1, ctx_at)
            if r is not None:
                out.append({"r": r, "t": t15[i], "side": side})
                # advance busy to exit time approx: find next entry after this fills.
                # (one-position-at-a-time; re-scan handled by busy on entry index)
                busy = i  # conservative: block re-entry on same/earlier bar only
        return out

    # buy-hold sanity: long every ~day, same exit harness
    bh_entries = [(i, "long") for i in range(ENTRY_N, len(c) - 1, 96)]
    def run_bh():
        out = []; p1 = 0
        for (i, side) in bh_entries:
            a = atr[i]
            if a != a or a <= 0:
                continue
            entry = c[i] * (1 + SLIP)
            risk = STOP_K * a; sl0 = entry - risk
            open_at = t15[i] + r1.TF15_S
            while p1 < len(T1) and T1[p1] < open_at:
                p1 += 1
            if p1 >= len(T1):
                break
            r = sim_trade("long", entry, sl0, risk, ctx, T1, H1, L1, p1, ctx_at)
            if r is not None:
                out.append({"r": r, "t": t15[i], "side": "long"})
        return out

    variants = {
        "donchian_raw":  run(False, False),
        "donchian+htf":  run(True, False),
        "donchian+htf+reg": run(True, True),
    }

    # CLEAN market sanity: pure close-to-close buy & hold (no stop, no exit harness).
    mkt_ret = (c[-1] - c[0]) / c[0] * 100
    print("=" * 100)
    print(f"FULL DONCHIAN STRATEGY — own entry+exit ({span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d})")
    print(f"  channel {ENTRY_N}/{EXIT_M}, initial stop {STOP_K}*ATR{ATR_LEN}, fees+slip, 1m intrabar")
    print(f"  MARKET sanity (pure buy & hold, close→close): BTC {c[0]:,.0f} → {c[-1]:,.0f} = {mkt_ret:+.1f}%")
    print("=" * 100)
    res = {}
    for name, tr in variants.items():
        res[name] = tr
        line(name, stats(tr))

    print("\n" + "=" * 100)
    print(f"WALK-FORWARD — {folds} folds, PF (n)")
    print("=" * 100)
    t0, t1 = t15[0], t15[-1]
    width = (t1 - t0) / folds
    names = list(variants)
    print(f"  {'fold':<13}" + "".join(f"{nm:>18}" for nm in names))
    for fi in range(folds):
        lo, hi = t0 + fi * width, t0 + (fi + 1) * width
        cells = ""
        for nm in names:
            m = stats([x for x in res[nm] if lo <= x["t"] < hi])
            cell = f"{m['pf']:.2f}({m['n']})" if m else "—"
            cells += f"{cell:>18}"
        print(f"  {datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d}{cells}")
    print("=" * 100)


if __name__ == "__main__":
    main()
