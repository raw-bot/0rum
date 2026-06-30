"""Experiment: do ICT-derived FILTERS add edge to our arm-fire entries?

Maps the testable ICT concepts (docs/ict-price-action-concepts.md) onto our existing
proven entries and measures each as a one-variable filter vs the PF 0.80 baseline,
with walk-forward. We do NOT re-test exits or existing filters (Levers A/C already
killed those); we test the directional/temporal filters ICT has that we lack.

Filters:
  htf_bias  : keep an entry only if its direction agrees with the higher-timeframe
              trend. HTF = 15m aggregated to 4H (16 bars); bias = up when the 4H EMA
              is rising AND 4H close > 4H EMA, down when falling AND close < EMA.
              (ICT: trade continuations with the HTF bias; BOS, not CHoCH.)
  killzone  : keep an entry only if the bar opens during London (07-10 UTC) or
              New York (12-15 UTC) kill zones.
  ote_depth : keep an entry only if the pullback into the entry sits in 61.8-78.6%
              (OTE) of the last swing — reject shallow (<38.2%) and over-deep (>88%).
  htf+kz    : htf_bias AND killzone.

Each filter is a pure SUBSET of the baseline entries (it can only remove trades), so
a filter "helps" only if removing those trades RAISES PF and holds up in walk-forward.

Usage: uv run python scripts/experiment_ict_filters.py [n_bars] [--folds K]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import baseline_ak_macd as base
import backtest_parity as bp

from hermes_trading.external.ak_macd import AkMacdParams, compute_state
from hermes_trading.external.bracket import compute_bracket


# --------------------------------------------------------------- HTF (4H) bias
def htf_bias_series(bars, agg=16, ema_len=50):
    """Per-15m-bar HTF bias in {+1,-1,0}, computed from a 4H aggregation (16x15m).
    Bias is known only on CLOSED 4H bars (no lookahead): each 15m bar inherits the
    bias of the most recently CLOSED 4H bar."""
    closes = [b["close"] for b in bars]
    n = len(closes)
    # build 4H closes at their close-bar index
    h4_idx, h4_close = [], []
    for end in range(agg - 1, n, agg):
        h4_idx.append(end)
        h4_close.append(closes[end])
    if len(h4_close) < ema_len + 2:
        return [0] * n
    a = 2 / (ema_len + 1)
    ema = [h4_close[0]]
    for x in h4_close[1:]:
        ema.append(a * x + (1 - a) * ema[-1])
    bias4 = [0] * len(h4_close)
    for k in range(1, len(h4_close)):
        rising = ema[k] > ema[k - 1]
        if rising and h4_close[k] > ema[k]:
            bias4[k] = 1
        elif (not rising) and h4_close[k] < ema[k]:
            bias4[k] = -1
    # propagate each closed 4H bias forward to 15m bars AFTER that 4H bar closed
    out = [0] * n
    kk = 0
    for i in range(n):
        while kk + 1 < len(h4_idx) and h4_idx[kk + 1] <= i:
            kk += 1
        # only apply once the 4H bar has fully closed (i strictly after its close idx)
        out[i] = bias4[kk] if i > h4_idx[kk] else (bias4[kk - 1] if kk > 0 else 0)
    return out


# ------------------------------------------------------------------ kill zones
def in_killzone(epoch_s):
    h = datetime.fromtimestamp(epoch_s, timezone.utc).hour
    return (7 <= h < 10) or (12 <= h < 15)


# ------------------------------------------------------------- OTE pullback depth
def ote_ok(st, t, side, swing=20):
    """Pullback depth of the entry inside the last `swing` bars' range, as a Fib %.
    Long: 0% at swing high, 100% at swing low -> OTE if entry retraced 61.8-78.6%
    DOWN from the high. Short mirror. Reject shallow (<38.2) / over-deep (>88)."""
    lo = min(st.lows[max(0, t - swing + 1): t + 1])
    hi = max(st.highs[max(0, t - swing + 1): t + 1])
    rng = hi - lo
    if rng <= 0:
        return False
    c = st.closes[t]
    if side == "long":
        depth = (hi - c) / rng       # how far below the high we entered
    else:
        depth = (c - lo) / rng       # how far above the low we entered
    return 0.618 <= depth <= 0.88    # OTE sweet spot, allow to 0.88 with caution


# ----------------------------------------------------------------------- engine
def signals_from(bars, p, entries):
    st = compute_state(bars, p)
    sw = p.swing_look
    out = []
    for (t, side) in entries:
        try:
            br = compute_bracket(entry_price=st.closes[t], baseline_at_entry=st.baseline[t],
                                 recent_low=min(st.lows[max(0, t - sw + 1): t + 1]),
                                 recent_high=max(st.highs[max(0, t - sw + 1): t + 1]),
                                 direction=side, rr=base.RR)
        except ValueError:
            continue
        out.append((t, side, br))
    return out


def m1(bars, sig):
    return base.metrics(base.simulate(sig, bars, base.LEVERAGE_SWEEP[0]))


def line(name, m):
    if not m:
        print(f"  {name:<12}: no trades"); return
    print(f"  {name:<12}: {m['n']:>4} tr | WR {m['winrate']:>5.1%} | PF {m['pf']:.2f} | "
          f"exp {m['exp_r_mult']:+.3f}R | net ${m['net']:+,.0f} | maxDD {m['max_dd_pct']:.0%}")


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

    p = AkMacdParams()
    print(f"Fetching ~{n_target} BTCUSDT 15m bars ...", flush=True)
    bars = base.fetch_klines(n=n_target)
    t = [b["time"] for b in bars]
    span = (datetime.fromtimestamp(t[0], timezone.utc), datetime.fromtimestamp(t[-1], timezone.utc))
    print(f"Got {len(bars)} bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}\n", flush=True)

    st = compute_state(bars, p)
    base_entries = bp.confirmations(bars, p)        # proven arm-fire entries
    bias = htf_bias_series(bars)

    def keep(entries, mode):
        out = []
        for (t_, side) in entries:
            if mode == "htf_bias" and bias[t_] != (1 if side == "long" else -1):
                continue
            if mode == "killzone" and not in_killzone(bars[t_]["time"]):
                continue
            if mode == "ote_depth" and not ote_ok(st, t_, side):
                continue
            if mode == "htf_kz" and not (
                bias[t_] == (1 if side == "long" else -1) and in_killzone(bars[t_]["time"])):
                continue
            out.append((t_, side))
        return out

    variants = {
        "baseline":  base_entries,
        "htf_bias":  keep(base_entries, "htf_bias"),
        "killzone":  keep(base_entries, "killzone"),
        "ote_depth": keep(base_entries, "ote_depth"),
        "htf_kz":    keep(base_entries, "htf_kz"),
    }

    print("=" * 96)
    print("FULL SAMPLE — ICT filters on arm-fire entries (1x, same sizing/exit harness)")
    print("=" * 96)
    sigs, mets = {}, {}
    for name, ent in variants.items():
        sigs[name] = signals_from(bars, p, ent)
        mets[name] = m1(bars, sigs[name])
        line(name, mets[name])

    print(f"\n  baseline PF = {mets['baseline']['pf']:.2f}. A filter helps only if PF rises "
          f"AND holds across folds (it can only REMOVE trades).")

    print("\n" + "=" * 96)
    print(f"WALK-FORWARD — {folds} folds, PF per variant")
    print("=" * 96)
    t0, t1 = t[0], t[-1]
    width = (t1 - t0) / folds
    print(f"  {'fold':<14}" + "".join(f"{nm:>11}" for nm in variants))
    print("  " + "-" * (14 + 11 * len(variants)))
    for fi in range(folds):
        lo, hi = t0 + fi * width, t0 + (fi + 1) * width
        cells = ""
        for nm in variants:
            fs = [s for s in sigs[nm] if lo <= bars[s[0]]["time"] < hi]
            mm = m1(bars, fs)
            cell = f"{mm['pf']:.2f}({mm['n']})" if mm else "—"
            cells += f"{cell:>11}"
        print(f"  {datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d}{'':<3}{cells}")
    print("=" * 96)


if __name__ == "__main__":
    main()
