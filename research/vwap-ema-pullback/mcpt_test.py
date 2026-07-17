"""In-sample MCPT (Monte Carlo Permutation Test) on the VWAP-EMA pullback grid.

Vendored + adapted from neurotrader888/mcpt (MIT) — bar_permute.get_permutation.
Adaptation: the original permutes OHLC only; our signal is volume-dependent
(rolling VWAP), so volume is CARRIED with the intrabar shuffle index (perm1) —
each synthetic bar keeps its original (relative-OHLC, volume) tuple, preserving
the per-bar price/volume relationship while destroying temporal sequence.

Why this test: our parameter grid (16 cells) let us pick "vwap200 = grid winner".
That is exactly the selection-bias MCPT is built to catch. We RE-OPTIMIZE the same
grid on each permuted BTC series and ask: how often does noise, optimized just as
hard, match our real best profit factor? p = P(best_perm_PF >= best_real_PF).
p > 0.05 => the grid winner is likely noise-mining, not edge.
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "liquidation-continuation", "src"))
from run import signal_vwap_ema_pullback, EXIT, load_btc_4h  # noqa: E402
from backtest import run_backtest, compute_metrics, make_arrays  # noqa: E402

GRID = [(e, v) for e in (7, 9, 13, 21) for v in (30, 50, 100, 200)]
MIN_TRADES = 40
N_PERM = 200


def get_permutation(ohlcv, start_index=0, seed=None):
    """neurotrader888/mcpt get_permutation, extended to carry a `volume` column
    with the intrabar (perm1) shuffle. Single-market DataFrame only here."""
    rng = np.random.default_rng(seed)
    n_bars = len(ohlcv)
    log_bars = np.log(ohlcv[["open", "high", "low", "close"]])
    vol = ohlcv["volume"].to_numpy()

    perm_index = start_index + 1
    perm_n = n_bars - perm_index

    start_bar = log_bars.iloc[start_index].to_numpy()
    r_o = (log_bars["open"] - log_bars["close"].shift()).to_numpy()
    r_h = (log_bars["high"] - log_bars["open"]).to_numpy()
    r_l = (log_bars["low"] - log_bars["open"]).to_numpy()
    r_c = (log_bars["close"] - log_bars["open"]).to_numpy()

    rel_o = r_o[perm_index:]
    rel_h, rel_l, rel_c = r_h[perm_index:], r_l[perm_index:], r_c[perm_index:]
    rel_v = vol[perm_index:]

    idx = np.arange(perm_n)
    perm1 = rng.permutation(idx)           # intrabar h/l/c + volume, shuffled together
    perm2 = rng.permutation(idx)           # gaps, shuffled independently
    rel_h, rel_l, rel_c, rel_v = rel_h[perm1], rel_l[perm1], rel_c[perm1], rel_v[perm1]
    rel_o = rel_o[perm2]

    perm_bars = np.zeros((n_bars, 4))
    perm_bars[:start_index] = log_bars.to_numpy()[:start_index]
    perm_bars[start_index] = start_bar
    for i in range(perm_index, n_bars):
        k = i - perm_index
        perm_bars[i, 0] = perm_bars[i - 1, 3] + rel_o[k]
        perm_bars[i, 1] = perm_bars[i, 0] + rel_h[k]
        perm_bars[i, 2] = perm_bars[i, 0] + rel_l[k]
        perm_bars[i, 3] = perm_bars[i, 0] + rel_c[k]
    perm_bars = np.exp(perm_bars)

    out = pd.DataFrame(perm_bars, index=ohlcv.index, columns=["open", "high", "low", "close"])
    out["volume"] = ohlcv["volume"].to_numpy()  # base; overwrite permuted region
    v = ohlcv["volume"].to_numpy().copy()
    v[perm_index:] = rel_v
    out["volume"] = v
    pc = out["close"].shift(1)
    tr = pd.concat([out["high"] - out["low"], (out["high"] - pc).abs(), (out["low"] - pc).abs()], axis=1).max(axis=1)
    out["atr_pct"] = tr.rolling(14, min_periods=5).mean() / out["close"]
    return out


def best_pf(df):
    """Optimize the grid: return the max profit factor over the grid (min-trades gated)."""
    arrays = make_arrays(df)
    best = 0.0
    best_cell = None
    for ema_len, vwap_len in GRID:
        idx, d = signal_vwap_ema_pullback(df, ema_len=ema_len, vwap_len=vwap_len)
        m = compute_metrics(run_backtest(df, idx, d, arrays=arrays, **EXIT))
        if m.get("n_trades", 0) < MIN_TRADES:
            continue
        pf = m.get("profit_factor")
        if pf is None or not np.isfinite(pf):
            continue
        if pf > best:
            best, best_cell = pf, (ema_len, vwap_len)
    return best, best_cell


def main():
    df = load_btc_4h()
    real_pf, cell = best_pf(df)
    print(f"BTC 4h {df.index[0].date()} -> {df.index[-1].date()}  ({len(df)} bars)")
    print(f"Real best PF over grid: {real_pf:.3f} at ema/vwap={cell}")
    print(f"In-sample MCPT: re-optimizing the SAME grid on {N_PERM} permuted series...")

    perm_better = 1  # count real as one (neurotrader convention)
    perm_pfs = []
    for i in range(1, N_PERM):
        perm = get_permutation(df, seed=i)
        p_pf, _ = best_pf(perm)
        perm_pfs.append(p_pf)
        if p_pf >= real_pf:
            perm_better += 1
        if i % 25 == 0:
            print(f"  {i}/{N_PERM}  perm_better={perm_better}  running p={perm_better/(i+1):.3f}", flush=True)

    pval = perm_better / N_PERM
    pp = np.array(perm_pfs)
    print(f"\nIn-sample MCPT p-value: {pval:.3f}  (perm best-PF >= real {real_pf:.3f} in {perm_better}/{N_PERM})")
    print(f"permuted best-PF distribution: median={np.median(pp):.3f}  "
          f"p90={np.percentile(pp,90):.3f}  max={pp.max():.3f}")
    verdict = ("SIGNIFICANT — grid edge survives selection-bias test" if pval <= 0.05
               else "NOT significant — grid 'winner' is consistent with optimized noise")
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
