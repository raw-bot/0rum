"""Strategy research SCREEN — does ANY entry family have directional edge on BTC 15m?

Honest, belief-free screen: take several well-known mechanical entry rules, run them
ALL through the SAME uniform ATR bracket exit (so only the ENTRY differs), and rank
by profit factor + walk-forward consistency. The point is to find an entry that
crosses PF 1.0 OUT-OF-SAMPLE, not to confirm any prior.

Uniform exit (identical for every strategy): SL = k*ATR(14), TP = RR*k*ATR, conservative
SL-first intrabar on 15m, fees+slippage, max-hold cap. Fixed literature params, no tuning.

Entry rules (closed bars only):
  macd_akm   : our production AK MACD arm-fire brain (reference = current bot)
  donchian   : breakout of the prior 20-bar high/low (trend)
  ema_cross  : EMA20 crosses EMA50 (trend)
  rsi_revert : RSI14 < 30 long / > 70 short (mean reversion)
  bb_revert  : close beyond Bollinger(20,2) band (mean reversion)
  fvg_ce     : ICT — enter at the 50% (CE) of an unfilled 3-bar FVG (the only ICT
               concept that is a standalone mechanical signal)
  always_long: benchmark (buy-and-hold-ish, every bar)

Usage: uv run python scripts/research_strategy_screen.py [n_bars] [--folds K]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import baseline_ak_macd as base
import backtest_parity as bp

from orum.external.ak_macd import AkMacdParams, compute_state

# ---- uniform exit / cost params (fixed) ----
ATR_LEN = 14
SL_ATR = 1.5
RR = 1.5
FEE = 0.0004
SLIP = 0.0003
MAX_HOLD = 96          # 24h on 15m — avoid never-resolving trades


def ema(xs, n):
    a = 2 / (n + 1); out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def rma(xs, n):
    a = 1 / n; out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def sma(xs, n):
    return [sum(xs[max(0, i - n + 1): i + 1]) / len(xs[max(0, i - n + 1): i + 1]) for i in range(len(xs))]


def stdev(xs, n):
    out = []
    for i in range(len(xs)):
        w = xs[max(0, i - n + 1): i + 1]
        m = sum(w) / len(w)
        out.append((sum((x - m) ** 2 for x in w) / len(w)) ** 0.5)
    return out


def rsi(c, n=14):
    gains = [0.0]; losses = [0.0]
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag = rma(gains, n); al = rma(losses, n)
    out = []
    for i in range(len(c)):
        out.append(100.0 if al[i] == 0 else 100 - 100 / (1 + ag[i] / al[i]))
    return out


def true_range(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return tr


# ---------------------------------------------------------------- entry generators
def entries_macd(bars):
    p = AkMacdParams()
    return bp.confirmations(bars, p)            # (t, side)


def entries_donchian(h, l, c, n=20):
    out = []
    for i in range(n, len(c) - 1):
        hh = max(h[i - n:i]); ll = min(l[i - n:i])
        if c[i] > hh:
            out.append((i, "long"))
        elif c[i] < ll:
            out.append((i, "short"))
    return out


def entries_ema_cross(c, fast=20, slow=50):
    ef, es = ema(c, fast), ema(c, slow)
    out = []
    for i in range(slow + 1, len(c) - 1):
        if ef[i - 1] <= es[i - 1] and ef[i] > es[i]:
            out.append((i, "long"))
        elif ef[i - 1] >= es[i - 1] and ef[i] < es[i]:
            out.append((i, "short"))
    return out


def entries_rsi(c, n=14, lo=30, hi=70):
    r = rsi(c, n)
    out = []
    for i in range(n + 1, len(c) - 1):
        if r[i - 1] >= lo and r[i] < lo:
            out.append((i, "long"))
        elif r[i - 1] <= hi and r[i] > hi:
            out.append((i, "short"))
    return out


def entries_bb(c, n=20, k=2.0):
    m = sma(c, n); s = stdev(c, n)
    out = []
    for i in range(n + 1, len(c) - 1):
        up, dn = m[i] + k * s[i], m[i] - k * s[i]
        if c[i] < dn:
            out.append((i, "long"))
        elif c[i] > up:
            out.append((i, "short"))
    return out


def entries_fvg_ce(h, l, c, lookback=40):
    """ICT FVG + CE as a standalone entry. Bullish FVG at i: low[i] > high[i-2]
    (gap = [high[i-2], low[i]], CE = midpoint). Enter LONG the first later bar whose
    low trades down to the CE while the gap is still unfilled. Bearish mirror."""
    out = []
    n = len(c)
    active = []   # (formed_idx, side, ce, gap_lo, gap_hi)
    for i in range(2, n - 1):
        # register new FVGs
        if l[i] > h[i - 2]:
            ce = (h[i - 2] + l[i]) / 2
            active.append((i, "long", ce, h[i - 2], l[i]))
        if h[i] < l[i - 2]:
            ce = (h[i] + l[i - 2]) / 2
            active.append((i, "short", ce, h[i], l[i - 2]))
        # check returns to CE on this bar; expire stale / filled
        still = []
        for (fi, side, ce, glo, ghi) in active:
            if i - fi > lookback:
                continue
            if side == "long":
                if l[i] <= glo:            # gap fully filled -> invalid
                    continue
                if l[i] <= ce <= h[i] and i > fi:
                    out.append((i, "long")); continue
            else:
                if h[i] >= ghi:
                    continue
                if l[i] <= ce <= h[i] and i > fi:
                    out.append((i, "short")); continue
            still.append((fi, side, ce, glo, ghi))
        active = still
    out.sort()
    return out


def entries_always_long(c):
    return [(i, "long") for i in range(50, len(c) - 1, 8)]   # sparse buy benchmark


# ------------------------------------------------------------------------ exit sim
def sim(entries, h, l, c, atr):
    """Uniform ATR bracket, conservative SL-first, one position at a time. Returns
    list of {r, t}."""
    n = len(c)
    out = []
    busy = -1
    for (i, side) in entries:
        if i <= busy or i + 1 >= n:
            continue
        a = atr[i]
        if a <= 0:
            continue
        entry = c[i] * (1 + SLIP) if side == "long" else c[i] * (1 - SLIP)
        risk = SL_ATR * a
        if side == "long":
            sl, tp = entry - risk, entry + RR * risk
        else:
            sl, tp = entry + risk, entry - RR * risk
        r = None
        for j in range(i + 1, min(n, i + 1 + MAX_HOLD)):
            hit_sl = (l[j] <= sl) if side == "long" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if side == "long" else (l[j] <= tp)
            if hit_sl:
                fill = sl * (1 - SLIP) if side == "long" else sl * (1 + SLIP)
                g = (fill - entry) if side == "long" else (entry - fill)
                r = (g - FEE * (entry + fill)) / risk; busy = j; break
            if hit_tp:
                g = (tp - entry) if side == "long" else (entry - tp)
                r = (g - FEE * (entry + tp)) / risk; busy = j; break
        if r is not None:
            out.append({"r": r, "t": i})
    return out


def stats(trades):
    if not trades:
        return None
    rs = [x["r"] for x in trades]
    w = [x for x in trades if x["r"] > 0]
    gl = -sum(x["r"] for x in trades if x["r"] <= 0)
    pf = (sum(x["r"] for x in w) / gl) if gl > 0 else float("inf")
    return {"n": len(rs), "wr": len(w) / len(rs), "net_r": sum(rs),
            "exp_r": sum(rs) / len(rs), "pf": pf}


def line(name, m):
    if not m:
        print(f"  {name:<12}: no trades"); return
    print(f"  {name:<12}: {m['n']:>5} tr | WR {m['wr']:>5.1%} | PF {m['pf']:.2f} | "
          f"exp {m['exp_r']:+.3f}R | net {m['net_r']:+7.0f}R")


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

    print(f"Fetching ~{n_target} BTCUSDT 15m bars ...", flush=True)
    bars = base.fetch_klines(n=n_target)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; t = [b["time"] for b in bars]
    atr = rma(true_range(h, l, c), ATR_LEN)
    span = (datetime.fromtimestamp(t[0], timezone.utc), datetime.fromtimestamp(t[-1], timezone.utc))
    mkt = (c[-1] - c[0]) / c[0] * 100
    print(f"Got {len(c)} bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}", flush=True)
    print(f"MARKET (buy&hold close→close): BTC {c[0]:,.0f} → {c[-1]:,.0f} = {mkt:+.1f}%", flush=True)
    print(f"Uniform exit: SL {SL_ATR}*ATR, RR {RR}, fees {FEE:.2%}+slip {SLIP:.2%}, maxhold {MAX_HOLD}\n",
          flush=True)

    strategies = {
        "macd_akm":    entries_macd(bars),
        "donchian":    entries_donchian(h, l, c),
        "ema_cross":   entries_ema_cross(c),
        "rsi_revert":  entries_rsi(c),
        "bb_revert":   entries_bb(c),
        "fvg_ce":      entries_fvg_ce(h, l, c),
        "always_long": entries_always_long(c),
    }

    print("=" * 84)
    print("FULL SAMPLE — directional edge of each entry family (uniform exit)")
    print("=" * 84)
    res = {}
    for name, ent in strategies.items():
        tr = sim(ent, h, l, c, atr)
        res[name] = (stats(tr), tr)
        line(name, res[name][0])

    print("\n" + "=" * 84)
    print(f"WALK-FORWARD — {folds} folds, PF (n trades) per strategy")
    print("=" * 84)
    t0, t1 = t[0], t[-1]
    width = (t1 - t0) / folds
    names = list(strategies)
    print(f"  {'fold':<13}" + "".join(f"{nm:>13}" for nm in names))
    print("  " + "-" * (13 + 13 * len(names)))
    pf_track = {nm: [] for nm in names}
    for fi in range(folds):
        lo, hi = t0 + fi * width, t0 + (fi + 1) * width
        cells = ""
        for nm in names:
            ftr = [x for x in res[nm][1] if lo <= t[x["t"]] < hi]
            m = stats(ftr)
            cell = f"{m['pf']:.2f}({m['n']})" if m else "—"
            if m:
                pf_track[nm].append(m["pf"])
            cells += f"{cell:>13}"
        print(f"  {datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d}{cells}")

    print("\n" + "=" * 84)
    print("RANKING — strategies with PF > 1.0 in the MAJORITY of folds (the survivors)")
    print("=" * 84)
    scored = []
    for nm in names:
        pfs = pf_track[nm]
        if not pfs:
            continue
        wins = sum(1 for x in pfs if x > 1.0)
        full = res[nm][0]
        scored.append((wins, full["pf"] if full else 0, nm, pfs))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for wins, fullpf, nm, pfs in scored:
        flag = "✅ SURVIVOR" if wins > folds / 2 and fullpf > 1.0 else ""
        print(f"  {nm:<12}: full PF {fullpf:.2f} | folds PF>1: {wins}/{len(pfs)}  {flag}")
    print("=" * 84)


if __name__ == "__main__":
    main()
