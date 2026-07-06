"""EXIT LAB — keep the AK MACD entries EXACTLY, swap only the exit (Phase 1-2).

The entries are the production arm-fire confirmations (proven parity, untouched).
Every exit starts from the SAME initial risk distance (compute_bracket SL) so R is
comparable across exits. Exits are simulated on REAL 1m intrabar candles
(conservative adverse-before-favorable). This is ONLY 'AK MACD with a better exit'
— the full Donchian strategy (own entry) is a SEPARATE script, never mixed here.

Exits tested:
  bracket_2R   : current behaviour — fixed SL + TP at 2R (sanity anchor: must match
                 the -249.8R we already measured in backtest_runner_1m).
  donchian_tr  : ratchet the stop up to the N-bar Donchian low (long)/high (short),
                 no TP cap — ride until the channel stop is hit.
  opp_channel  : no TP; exit when a closed 15m bar breaks the OPPOSITE N-bar channel.
  atr_trail    : wide ATR trail — stop = peak ∓ k*ATR (k=3), ratcheting, no cap.
  time_stop    : exit at market after H closed 15m bars (or initial SL first).
  structural   : exit when price breaks the last confirmed swing low(HL)/high(LH).

Metrics: expectancy R, PF, max DD (R), capture = realized R / natural MFE, avg winner
R, gain distribution, and a per-regime breakdown (regime label at entry).

Usage: uv run python scripts/exit_lab.py [n_15m_bars]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import baseline_ak_macd as base
import backtest_parity as bp
import backtest_runner_1m as r1          # reuse fetch_1m, atr15, natural_mfe, TF15_S

from orum.external.ak_macd import AkMacdParams, compute_state
from orum.external.bracket import compute_bracket

DON_N = 20
ATR_K = 3.0
TIME_H = 32                              # 8h on 15m
PIVOT_L = 3
FEE = 0.0004
SLIP = 0.0003
RR_TP = 2.0


# --------------------------------------------------------------- 15m context series
def donchian(h, l, n):
    dl = [float("nan")] * len(h); dh = [float("nan")] * len(h)
    for i in range(len(h)):
        if i >= n:
            dl[i] = min(l[i - n:i]); dh[i] = max(h[i - n:i])
    return dl, dh


def swings(h, l, L):
    """Confirmed pivots (fractal, half-width L). last_low[i]/last_high[i] = price of
    the most recent pivot confirmed by bar i (causal: a pivot at p is known at p+L)."""
    n = len(h)
    piv_lo = [float("nan")] * n; piv_hi = [float("nan")] * n
    for p in range(L, n - L):
        if all(l[p] <= l[p - k] and l[p] <= l[p + k] for k in range(1, L + 1)):
            if p + L < n:
                piv_lo[p + L] = l[p]
        if all(h[p] >= h[p - k] and h[p] >= h[p + k] for k in range(1, L + 1)):
            if p + L < n:
                piv_hi[p + L] = h[p]
    last_lo = [float("nan")] * n; last_hi = [float("nan")] * n
    cl = ch = float("nan")
    for i in range(n):
        if piv_lo[i] == piv_lo[i]:
            cl = piv_lo[i]
        if piv_hi[i] == piv_hi[i]:
            ch = piv_hi[i]
        last_lo[i] = cl; last_hi[i] = ch
    return last_lo, last_hi


# --------------------------------------------------------------------- exit sims (1m)
def _net_r(side, entry, fill, risk):
    g = (fill - entry) if side == "long" else (entry - fill)
    return (g - FEE * (entry + fill)) / risk


def run_exit(kind, side, entry, sl0, risk, tp, ctx, T1, H1, L1, start, ctx_at):
    """Simulate one trade on 1m bars. ctx_at(j)->most-recent-closed-15m index.
    ctx holds the 15m series. Returns realized net R or None."""
    long = side == "long"
    stop = sl0
    peak = entry
    bars15_entry = ctx_at(T1[start]) if start < len(T1) else None
    for j in range(start, len(T1)):
        i15 = ctx_at(T1[j])
        # update trailing stop per exit kind (uses last CLOSED 15m context)
        if kind == "donchian_tr" and i15 is not None:
            ch = ctx["don_lo"][i15] if long else ctx["don_hi"][i15]
            if ch == ch:
                stop = max(stop, ch) if long else min(stop, ch)
        elif kind == "atr_trail" and i15 is not None:
            a = ctx["atr"][i15]
            if a == a:
                cand = (peak - ATR_K * a) if long else (peak + ATR_K * a)
                stop = max(stop, cand) if long else min(stop, cand)
        elif kind == "structural" and i15 is not None:
            lvl = ctx["sw_lo"][i15] if long else ctx["sw_hi"][i15]
            if lvl == lvl:
                stop = max(stop, lvl) if long else min(stop, lvl)
        # check stop hit (adverse first, conservative)
        adverse = (L1[j] <= stop) if long else (H1[j] >= stop)
        if adverse:
            fill = stop * (1 - SLIP) if long else stop * (1 + SLIP)
            return _net_r(side, entry, fill, risk)
        # fixed TP (only bracket_2R)
        if kind == "bracket_2R":
            fav = (H1[j] >= tp) if long else (L1[j] <= tp)
            if fav:
                return _net_r(side, entry, tp, risk)
        # opposite-channel / time-stop exits at the close of a 15m bar
        if kind == "opp_channel" and i15 is not None:
            opp = ctx["don_lo"][i15] if long else ctx["don_hi"][i15]
            c15 = ctx["close"][i15]
            if opp == opp and ((c15 < opp) if long else (c15 > opp)):
                return _net_r(side, entry, c15 * (1 - SLIP) if long else c15 * (1 + SLIP), risk)
        if kind == "time_stop" and i15 is not None and bars15_entry is not None:
            if i15 - bars15_entry >= TIME_H:
                c15 = ctx["close"][i15]
                return _net_r(side, entry, c15 * (1 - SLIP) if long else c15 * (1 + SLIP), risk)
        peak = max(peak, H1[j]) if long else min(peak, L1[j])
    return None


# ------------------------------------------------------------------------- metrics
def stats(trs):
    if not trs:
        return None
    rs = [x["r"] for x in trs]
    w = [x for x in trs if x["r"] > 0]
    gl = -sum(x["r"] for x in trs if x["r"] <= 0)
    pf = (sum(x["r"] for x in w) / gl) if gl > 0 else float("inf")
    eq = 0.0; peak = 0.0; dd = 0.0
    for x in trs:
        eq += x["r"]; peak = max(peak, eq); dd = max(dd, peak - eq)
    cap = [x["r"] / x["mfe"] for x in w if x["mfe"] > 0]
    return {"n": len(trs), "wr": len(w) / len(trs), "pf": pf,
            "exp": sum(rs) / len(rs), "net": sum(rs), "maxdd": dd,
            "avg_win": (sum(x["r"] for x in w) / len(w)) if w else 0.0,
            "capture": (sum(cap) / len(cap)) if cap else 0.0}


def line(name, m):
    if not m:
        print(f"  {name:<12}: no trades"); return
    print(f"  {name:<12}: {m['n']:>4} | WR {m['wr']:>5.1%} | PF {m['pf']:.2f} | "
          f"exp {m['exp']:+.3f}R | net {m['net']:+7.1f}R | maxDD {m['maxdd']:5.1f}R | "
          f"avgWin {m['avg_win']:+.2f}R | capt {m['capture']:.0%}")


def dist(trs):
    buckets = [("<-1R", lambda r: r <= -1), ("-1..0", lambda r: -1 < r <= 0),
               ("0..1", lambda r: 0 < r <= 1), ("1..2", lambda r: 1 < r <= 2),
               ("2..4", lambda r: 2 < r <= 4), (">4R", lambda r: r > 4)]
    out = []
    for label, f in buckets:
        out.append(f"{label}:{sum(1 for x in trs if f(x['r']))}")
    return "  ".join(out)


EXITS = ["bracket_2R", "donchian_tr", "opp_channel", "atr_trail", "time_stop", "structural"]


def main():
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 50000
    p = AkMacdParams()
    print(f"Fetching ~{n_target} 15m bars ...", flush=True)
    bars = base.fetch_klines(n=n_target)
    h = [b["high"] for b in bars]; l = [b["low"] for b in bars]
    c = [b["close"] for b in bars]; t15 = [b["time"] for b in bars]
    span = (datetime.fromtimestamp(t15[0], timezone.utc), datetime.fromtimestamp(t15[-1], timezone.utc))
    print(f"Got {len(bars)} 15m bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}", flush=True)

    st = compute_state(bars, p)
    entries = bp.confirmations(bars, p)
    regime = bp._regime_labels(st.closes)
    A15 = r1.atr15(bars, p.atr_len)
    don_lo, don_hi = donchian(h, l, DON_N)
    sw_lo, sw_hi = swings(h, l, PIVOT_L)
    ctx = {"don_lo": don_lo, "don_hi": don_hi, "atr": A15, "sw_lo": sw_lo,
           "sw_hi": sw_hi, "close": c}
    print(f"Entries (AK MACD, unchanged): {len(entries)}", flush=True)

    print("Loading 1m intrabar (cached) ...", flush=True)
    os.environ.setdefault("0RUM_SCRATCH", "/private/tmp/claude-501/-Users-cube/"
                          "970db104-8da7-4abe-bfec-be3cb9c5de36/scratchpad")
    r1.CACHE = os.environ["0RUM_SCRATCH"] + "/btc_1m_cache.json"
    T1, H1, L1 = r1.fetch_1m(t15[0], t15[-1] + r1.TF15_S)
    print(f"1m bars: {len(T1)}\n", flush=True)

    # 1m index -> most recent CLOSED 15m index (close time = t15[i]+900 <= ts)
    def make_ctx_at():
        import bisect
        close_times = [tt + r1.TF15_S for tt in t15]
        def ctx_at(ts):
            k = bisect.bisect_right(close_times, ts) - 1
            return k if k >= 0 else None
        return ctx_at
    ctx_at = make_ctx_at()

    sw = p.swing_look
    # precompute brackets + entry 1m start once
    prepared = []
    p1 = 0
    for (i, side) in entries:
        try:
            br = compute_bracket(entry_price=st.closes[i], baseline_at_entry=st.baseline[i],
                                 recent_low=min(st.lows[max(0, i - sw + 1): i + 1]),
                                 recent_high=max(st.highs[max(0, i - sw + 1): i + 1]),
                                 direction=side, rr=RR_TP)
        except ValueError:
            continue
        entry = st.closes[i] * (1 + SLIP) if side == "long" else st.closes[i] * (1 - SLIP)
        open_at = t15[i] + r1.TF15_S
        while p1 < len(T1) and T1[p1] < open_at:
            p1 += 1
        if p1 >= len(T1):
            break
        nat = r1.natural_mfe(side, entry, br.stop_loss_price, br.risk_distance, H1, L1, p1)
        prepared.append((i, side, entry, br, p1, nat))

    results = {k: [] for k in EXITS}
    for (i, side, entry, br, start, nat) in prepared:
        for k in EXITS:
            r = run_exit(k, side, entry, br.stop_loss_price, br.risk_distance,
                         br.take_profit_price, ctx, T1, H1, L1, start, ctx_at)
            if r is not None:
                results[k].append({"r": r, "mfe": nat, "dir": side, "t": t15[i],
                                   "regime": regime[i]})

    print("=" * 100)
    print(f"EXIT COMPARISON — AK MACD entries fixed, exit varied  ({span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d})")
    print("=" * 100)
    for k in EXITS:
        line(k, stats(results[k]))

    print("\n  Gain distribution (R buckets):")
    for k in EXITS:
        print(f"    {k:<12}: {dist(results[k])}")

    print("\n" + "=" * 100)
    print("PER-REGIME (PF | exp R | n) at entry — favorable / neutral / unfavorable")
    print("=" * 100)
    for k in EXITS:
        cells = ""
        for reg in ("favorable", "neutral", "unfavorable"):
            m = stats([x for x in results[k] if x["regime"] == reg])
            val = f"{m['pf']:.2f}/{m['exp']:+.2f}/{m['n']}" if m else "—"
            cells += f"  {reg[:5]}:{val}"
        print(f"  {k:<12}{cells}")
    print("=" * 100)


if __name__ == "__main__":
    main()
