"""Experiment: which entry FILTER is costing us, and does relaxing it actually help?

Motivated by the per-bar diagnosis (diag_signal.py): on clean downtrends the brain
never SHORTS because the sequenced trend->pullback requirement never completes, and
the volume gate is shut on low-volume drift. This script tests, as isolated
ONE-VARIABLE variants against the proven baseline, whether relaxing each filter
improves PF / expectancy — or just adds losing trades.

NO live code is touched. Each variant reuses the exact brain predicates; only the
toggled filter changes. With all toggles at their live defaults the confirmation
set is byte-identical to the production brain (proven by --verify), so any delta is
attributable solely to the toggled filter.

Variants:
  baseline   : live brain (all filters on)               <- the PF 0.80 reference
  no_seq     : drop the trend->pullback SEQUENCE gate
  no_vol     : drop the volume>SMA gate
  no_regime  : drop the regime filter
  no_seq_vol : drop sequence AND volume

Usage: uv run python scripts/experiment_entry_filters.py [n_bars] [--verify N] [--folds K]
"""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone

import baseline_ak_macd as base
import backtest_parity as bp

from hermes_trading.external.ak_macd import (
    ACTION_CONFIRMED, AkMacdParams, compute_state, evaluate_ak_macd_verdict,
    _flip_up, _flip_down, _is_num, _strictly_increasing, _strictly_decreasing,
    _sequenced_long, _sequenced_short, _candle_in_direction, _regime_at,
)
from hermes_trading.external.bracket import compute_bracket


def long_conditions(st, t, p, *, use_seq=True, use_vol=True):
    return (
        _is_num(st.macd[t]) and st.macd[t] > 0.0
        and _is_num(st.baseline[t]) and st.closes[t] > st.baseline[t]
        and (not use_vol or (_is_num(st.vol_ma[t]) and st.volumes[t] > st.vol_ma[t]))
        and (not use_seq or _sequenced_long(st, t, p))
        and _candle_in_direction(st, t, p, long=True)
    )


def short_conditions(st, t, p, *, use_seq=True, use_vol=True):
    return (
        _is_num(st.macd[t]) and st.macd[t] < 0.0
        and _is_num(st.baseline[t]) and st.closes[t] < st.baseline[t]
        and (not use_vol or (_is_num(st.vol_ma[t]) and st.volumes[t] > st.vol_ma[t]))
        and (not use_seq or _sequenced_short(st, t, p))
        and _candle_in_direction(st, t, p, long=False)
    )


def confirmations(bars, p, *, use_seq=True, use_vol=True, use_regime=True):
    """Parameterized replay of the production candidate machine. With (use_seq,
    use_vol)=True and use_regime=p.regime_filter it equals bp.confirmations exactly."""
    st = compute_state(bars, p)
    macd = st.macd
    n = len(macd)
    regime = bp._regime_labels(st.closes) if use_regime else [""] * n
    W, cb, warmup = p.candidate_window_bars, p.confirmation_bars, p.warmup
    cand = None
    out = []
    for t in range(2, n):
        fu, fd = _flip_up(macd, t), _flip_down(macd, t)
        if fu:
            cand = (t, "long")
        elif fd and p.allow_short:
            cand = (t, "short")
        elif cand is not None:
            c, side = cand
            if t > c + W:
                cand = None
            elif side == "long":
                if not (_is_num(macd[t]) and _is_num(macd[t - 1]) and macd[t] > macd[t - 1]):
                    cand = None
                elif not _strictly_increasing(macd, t, cb):
                    pass
                elif not long_conditions(st, t, p, use_seq=use_seq, use_vol=use_vol):
                    pass
                elif use_regime and regime[t] == "unfavorable":
                    pass
                elif t >= warmup:
                    out.append((t, "long")); cand = None
                else:
                    cand = None
            else:
                if not (_is_num(macd[t]) and _is_num(macd[t - 1]) and macd[t] < macd[t - 1]):
                    cand = None
                elif not _strictly_decreasing(macd, t, cb):
                    pass
                elif not short_conditions(st, t, p, use_seq=use_seq, use_vol=use_vol):
                    pass
                elif use_regime and regime[t] == "favorable":
                    pass
                elif t >= warmup:
                    out.append((t, "short")); cand = None
                else:
                    cand = None
    return out


def to_signals(bars, p, conf):
    st = compute_state(bars, p)
    sw = p.swing_look
    sig = []
    for (t, side) in conf:
        try:
            br = compute_bracket(entry_price=st.closes[t], baseline_at_entry=st.baseline[t],
                                 recent_low=min(st.lows[max(0, t - sw + 1): t + 1]),
                                 recent_high=max(st.highs[max(0, t - sw + 1): t + 1]),
                                 direction=side, rr=base.RR)
        except ValueError:
            continue
        sig.append((t, side, br))
    return sig


def metrics_for(bars, sig):
    res = base.simulate(sig, bars, base.LEVERAGE_SWEEP[0])  # 1x
    return base.metrics(res), res


def verify(bars, p, n):
    n = min(n, len(bars))
    cts = [{**b, "ts": int(b["time"]) * 1000} for b in bars]
    real = set()
    for i in range(p.warmup, n):
        v = evaluate_ak_macd_verdict(cts[: i + 1], p, symbol="BTCUSD")
        if v.action == ACTION_CONFIRMED:
            real.add((i, v.side))
    mine = {(t, s) for (t, s) in confirmations(bars, p, use_regime=p.regime_filter) if t < n}
    if mine != real:
        print(f"❌ PARITY FAIL: {sorted(mine ^ real)[:10]}"); raise SystemExit(1)
    print(f"✅ parity proven over first {n} bars ({len(real)} confirmations)\n")


VARIANTS = {
    "baseline":   dict(use_seq=True,  use_vol=True,  use_regime=True),
    "no_seq":     dict(use_seq=False, use_vol=True,  use_regime=True),
    "no_vol":     dict(use_seq=True,  use_vol=False, use_regime=True),
    "no_regime":  dict(use_seq=True,  use_vol=True,  use_regime=False),
    "no_seq_vol": dict(use_seq=False, use_vol=False, use_regime=True),
}


def line(name, m):
    if not m:
        print(f"  {name:<12}: no trades"); return
    print(f"  {name:<12}: {m['n']:>4} tr | WR {m['winrate']:>5.1%} | "
          f"PF {m['pf']:.2f} | exp {m['exp_r_mult']:+.3f}R | "
          f"net ${m['net']:+,.0f} | maxDD {m['max_dd_pct']:.0%}")


def main():
    argv = sys.argv[1:]
    n_target, n_verify, folds = 50000, 0, 5
    skip = False
    for k, a in enumerate(argv):
        if skip:
            skip = False; continue
        if a == "--verify":
            n_verify = int(argv[k + 1]); skip = True
        elif a == "--folds":
            folds = int(argv[k + 1]); skip = True
        elif a.isdigit():
            n_target = int(a)

    p = AkMacdParams()
    print(f"Fetching ~{n_target} BTCUSDT 15m bars ...", flush=True)
    bars = base.fetch_klines(n=n_target)
    t = [b["time"] for b in bars]
    span = (datetime.fromtimestamp(t[0], timezone.utc), datetime.fromtimestamp(t[-1], timezone.utc))
    print(f"Got {len(bars)} bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}\n", flush=True)

    if n_verify:
        verify(bars, p, n_verify)

    print("=" * 96)
    print("FULL SAMPLE — one-variable filter relaxations (1x, same sizing/exit harness)")
    print("=" * 96)
    results = {}
    for name, tog in VARIANTS.items():
        conf = confirmations(bars, p, **tog)
        sig = to_signals(bars, p, conf)
        m, _ = metrics_for(bars, sig)
        results[name] = (m, conf)
        line(name, m)

    base_m = results["baseline"][0]
    print(f"\n  (baseline = the live brain; a variant only 'helps' if PF goes UP "
          f"and stays > baseline across folds)")

    print("\n" + "=" * 96)
    print(f"WALK-FORWARD — {folds} folds, PF per variant (consistency check)")
    print("=" * 96)
    t0, t1 = t[0], t[-1]
    width = (t1 - t0) / folds
    # precompute per-variant signal lists with entry times
    sigs = {name: to_signals(bars, p, conf) for name, (m, conf) in results.items()}
    tbl_hdr = f"  {'fold':<26}" + "".join(f"{name:>12}" for name in VARIANTS)
    print(tbl_hdr); print("  " + "-" * (26 + 12 * len(VARIANTS)))
    for fi in range(folds):
        lo, hi = t0 + fi * width, t0 + (fi + 1) * width
        cells = ""
        for name in VARIANTS:
            fsig = [s for s in sigs[name] if lo <= bars[s[0]]["time"] < hi]
            m, _ = metrics_for(bars, fsig)
            pf_str = f"{m['pf']:.2f}" if m else "—"
            cells += f"{pf_str:>12}"
        label = f"{datetime.fromtimestamp(lo, timezone.utc):%Y-%m-%d}"
        print(f"  {label:<26}{cells}")
    print("=" * 96)


if __name__ == "__main__":
    main()
