"""Parity backtest — the AK MACD strategy as it ACTUALLY trades (arm-fire brain).

WHY THIS EXISTS
---------------
The headline backtest numbers (baseline_ak_macd.py: PF ~0.77; backtest_ak_macd.py:
PF ~0.60) are produced by a LOOSE entry generator that uses ``ta.barssince()``
order-independent triggers. The LIVE bot does NOT trade that logic. It trades the
production brain in orum/external/ak_macd.py: a flip ARMS a candidate
that must be CONFIRMED within a window by a strictly-monotonic MACD sequence, the
sequenced trend->pullback order, the volume gate, AND a regime filter (Lever A in
the diagnostic report).

So every existing backtest number describes a DIFFERENT, looser system than what
runs live. This script closes that gap: it measures the strategy through the
EXACT production decision code, on the same data window and the same sizing/exit
harness as baseline_ak_macd.py, so the only thing that differs from the baseline
is the entry logic. The PF/expectancy it prints are the first trustworthy numbers.

HOW PARITY IS GUARANTEED
------------------------
- Signals come from the brain's own ``compute_state`` + decision predicates
  (_flip_up/_flip_down, _strictly_increasing/_decreasing, _other_long/short_conditions)
  and the regime filter — not a reimplementation of the indicators.
- The ~20-line candidate state machine is replayed in a single O(n) pass (the
  brain's run_candidate_machine only returns the LAST bar's verdict; a backtest
  needs every confirmation).
- ``--verify N`` re-runs the REAL ``evaluate_ak_macd_verdict`` on growing prefixes
  for the first N bars and ASSERTS the confirmation set is identical. If the single
  pass ever diverged from the production brain, this aborts. Parity is proven, not
  assumed.
- Sizing, fees, slippage, one-position-at-a-time de-overlap and the SL/TP fill are
  reused verbatim from baseline_ak_macd.py (simulate/metrics), so the comparison is
  apples-to-apples: same harness, different entry.

Usage:
  uv run python scripts/backtest_parity.py [n_bars] [--verify N] [--no-baseline]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

# Reuse the baseline harness verbatim (same dir is on sys.path when run as a script).
import baseline_ak_macd as base

from orum.external.bracket import compute_bracket
from orum.external.ak_macd import (
    ACTION_CONFIRMED,
    AkMacdParams,
    _flip_down,
    _flip_up,
    _is_num,
    _other_long_conditions,
    _other_short_conditions,
    _strictly_decreasing,
    _strictly_increasing,
    compute_state,
    evaluate_ak_macd_verdict,
)
from orum.market_regime import rolling_return_regime


def _regime_labels(closes: list[float]) -> list[str]:
    """regime_label[t] == the brain's rolling_return_regime(closes[:t+1])["label"].
    The classifier only reads the last `lookback` (20) closes, so the windowed call
    below is byte-identical to the full-prefix call the brain makes — but O(n)."""
    n = len(closes)
    out = [""] * n
    for t in range(n):
        out[t] = rolling_return_regime(closes[max(0, t - 19): t + 1])["label"]
    return out


def confirmations(bars: list[dict], params: AkMacdParams) -> list[tuple[int, str]]:
    """Single-pass replay of the production candidate machine over the full buffer,
    collecting EVERY confirmed entry as (bar_index, side). Mirrors
    ak_macd.run_candidate_machine exactly; only difference is it yields all
    confirmations instead of the last bar's verdict."""
    st = compute_state(bars, params)
    macd = st.macd
    n = len(macd)
    regime = _regime_labels(st.closes) if params.regime_filter else [""] * n

    W = params.candidate_window_bars
    cb = params.confirmation_bars
    warmup = params.warmup
    candidate: tuple[int, str] | None = None  # (armed_index, side)
    out: list[tuple[int, str]] = []

    for t in range(2, n):
        fu = _flip_up(macd, t)
        fd = _flip_down(macd, t)
        if fu:                                    # flip ARMS / switches a candidate
            candidate = (t, "long")
        elif fd and params.allow_short:
            candidate = (t, "short")
        elif candidate is not None:
            c, side = candidate
            if t > c + W:                         # window exceeded -> expire
                candidate = None
            elif side == "long":
                if not (_is_num(macd[t]) and _is_num(macd[t - 1]) and macd[t] > macd[t - 1]):
                    candidate = None              # rising sequence broken -> expire
                elif not _strictly_increasing(macd, t, cb):
                    pass                          # rejected (macd) -> stay armed
                elif not _other_long_conditions(st, t, params):
                    pass                          # rejected (conditions) -> stay armed
                elif params.regime_filter and regime[t] == "unfavorable":
                    pass                          # rejected (regime) -> stay armed
                elif t >= warmup:                 # CONFIRMED (brain gates on warmup)
                    out.append((t, "long"))
                    candidate = None
                else:
                    candidate = None
            else:  # short — mirror
                if not (_is_num(macd[t]) and _is_num(macd[t - 1]) and macd[t] < macd[t - 1]):
                    candidate = None
                elif not _strictly_decreasing(macd, t, cb):
                    pass
                elif not _other_short_conditions(st, t, params):
                    pass
                elif params.regime_filter and regime[t] == "favorable":
                    pass
                elif t >= warmup:
                    out.append((t, "short"))
                    candidate = None
                else:
                    candidate = None
    return out


def generate_signals_parity(bars: list[dict], params: AkMacdParams):
    """Brain confirmations -> (i, direction, bracket), using the SAME compute_bracket
    the baseline + live engine use. Returns (signals, bars, degenerate)."""
    st = compute_state(bars, params)
    swing = params.swing_look
    signals = []
    degenerate = 0
    for (t, side) in confirmations(bars, params):
        lo = min(st.lows[max(0, t - swing + 1): t + 1])
        hi = max(st.highs[max(0, t - swing + 1): t + 1])
        try:
            br = compute_bracket(entry_price=st.closes[t], baseline_at_entry=st.baseline[t],
                                 recent_low=lo, recent_high=hi, direction=side, rr=base.RR)
        except ValueError:
            degenerate += 1
            continue
        signals.append((t, side, br))
    return signals, bars, degenerate


def verify_parity(bars: list[dict], params: AkMacdParams, n_verify: int) -> None:
    """Prove the single pass == the production brain by replaying the REAL
    evaluate_ak_macd_verdict on growing prefixes for the first n_verify bars.
    Aborts (SystemExit) on any divergence."""
    n_verify = min(n_verify, len(bars))
    candles_ts = [{**b, "ts": int(b["time"]) * 1000} for b in bars]
    real: set[tuple[int, str]] = set()
    for i in range(params.warmup, n_verify):
        v = evaluate_ak_macd_verdict(candles_ts[: i + 1], params, symbol="BTCUSD")
        if v.action == ACTION_CONFIRMED:
            real.add((i, v.side))
    mine = {(t, s) for (t, s) in confirmations(bars, params) if t < n_verify}
    if mine != real:
        only_mine = sorted(mine - real)[:10]
        only_real = sorted(real - mine)[:10]
        print(f"❌ PARITY CHECK FAILED over first {n_verify} bars")
        print(f"   single-pass only: {only_mine}")
        print(f"   real-brain  only: {only_real}")
        raise SystemExit(1)
    print(f"✅ PARITY PROVEN: single-pass confirmations identical to the production "
          f"brain over the first {n_verify} bars ({len(real)} confirmations).")


def _print_table(title: str, signals, bars):
    results = {lev: base.simulate(signals, bars, lev) for lev in base.LEVERAGE_SWEEP}
    mets = {lev: base.metrics(res) for lev, res in results.items()}
    b = results[base.LEVERAGE_SWEEP[0]]
    print("=" * 92)
    print(title)
    print("=" * 92)
    print(f"  Raw setups (valid brackets): {len(signals)}")
    print(f"  Accepted (traded)          : {len(b['accepted'])}")
    print(f"  Rejected by sizing         : {b['rejected']}")
    if not all(mets.values()):
        print("  (no accepted trades)\n")
        return mets
    hdr = (f"  {'metric':<22}" + "".join(f"{f'{lev:.0f}x':>13}" for lev in base.LEVERAGE_SWEEP))
    print(hdr)
    print("  " + "-" * (22 + 13 * len(base.LEVERAGE_SWEEP)))

    def row(label, fmt):
        cells = "".join(f"{fmt(mets[lev]):>13}" for lev in base.LEVERAGE_SWEEP)
        print(f"  {label:<22}{cells}")

    row("trades",         lambda m: f"{m['n']}")
    row("winrate",        lambda m: f"{m['winrate']:.1%}")
    row("profit factor",  lambda m: f"{m['pf']:.2f}")
    row("expectancy (R)", lambda m: f"{m['exp_r_mult']:+.3f}")
    row("net P&L ($)",    lambda m: f"${m['net']:+,.0f}")
    row("max drawdown %", lambda m: f"{m['max_dd_pct']:.1%}")
    row("max los. streak",lambda m: f"{m['max_losing_streak']}")
    print("=" * 92)
    return mets


def main() -> None:
    argv = sys.argv[1:]
    n_verify = 0
    run_baseline = True
    n_target = 50000
    skip = False
    for k, a in enumerate(argv):
        if skip:                          # this arg was consumed as --verify's value
            skip = False
            continue
        if a == "--verify":
            n_verify = int(argv[k + 1])
            skip = True
        elif a == "--no-baseline":
            run_baseline = False
        elif a.isdigit():
            n_target = int(a)

    params = AkMacdParams()
    print(f"Fetching ~{n_target} BTCUSDT 15m bars from Binance ...", flush=True)
    bars = base.fetch_klines(n=n_target)
    t = [b["time"] for b in bars]
    span = (datetime.fromtimestamp(t[0], timezone.utc), datetime.fromtimestamp(t[-1], timezone.utc))
    print(f"Got {len(bars)} bars: {span[0]:%Y-%m-%d} → {span[1]:%Y-%m-%d}\n", flush=True)
    print(f"Strategy params: {params}\n", flush=True)

    if n_verify:
        verify_parity(bars, params, n_verify)
        print()

    par_sig, _, par_deg = generate_signals_parity(bars, params)
    par_mets = _print_table("PARITY  — production arm-fire brain (what the bot ACTUALLY trades)",
                            par_sig, bars)

    if run_baseline:
        print()
        base_sig, _, base_deg = base.generate_signals(bars)
        _print_table("BASELINE — loose barssince() entries (the untrustworthy reference)",
                     base_sig, bars)
        # Delta callout at 1x
        lev = base.LEVERAGE_SWEEP[0]
        bm = base.metrics(base.simulate(base_sig, bars, lev))
        pm = par_mets.get(lev)
        if pm and bm:
            print("\nDELTA (1x):")
            print(f"  trades : {bm['n']:>6}  ->  {pm['n']:>6}   ({pm['n'] - bm['n']:+d})")
            print(f"  PF     : {bm['pf']:>6.2f}  ->  {pm['pf']:>6.2f}   ({pm['pf'] - bm['pf']:+.2f})")
            print(f"  exp (R): {bm['exp_r_mult']:>+6.3f}  ->  {pm['exp_r_mult']:>+6.3f}   "
                  f"({pm['exp_r_mult'] - bm['exp_r_mult']:+.3f})")


if __name__ == "__main__":
    main()
