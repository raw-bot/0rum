"""Run one (or all) of the 5 liquidation-continuation strategies end-to-end:
signal generation -> full-sample backtest -> chronological OOS test ->
random-entry baseline (same exit rule/costs) -> outlier-dominance check ->
parameter-robustness grid -> rejection-criteria verdict. Saves JSON to results/.
"""
import json
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import load_base, engineer_features
from strategies import STRATEGIES
from backtest import (run_backtest, compute_metrics, outlier_dominance_check,
                       random_baseline, chronological_split, make_arrays)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
os.makedirs(RESULTS, exist_ok=True)

DEFAULT_EXIT = dict(stop_mult=1.5, target_mult=2.0, max_hold=96, fee_bps=5.0, slip_bps=3.0)

MIN_TRADES = 50


def jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, pd.Timestamp):
        return str(o)
    raise TypeError(str(type(o)))


def long_frac_of(direction):
    direction = np.asarray(direction)
    if len(direction) == 0:
        return 0.5
    return float((direction == 1).mean())


def run_one(name, fn, feat, arrays, train_end_ts, exit_params=DEFAULT_EXIT, param_grid=None, n_random_sims=150):
    t0 = time.time()
    idx, direction = fn(feat)
    n_total_signals = len(idx)

    full_trades = run_backtest(feat, idx, direction, arrays=arrays, **exit_params)
    full_metrics = compute_metrics(full_trades)

    # chronological OOS: signals whose bar timestamp falls in test period
    sig_ts = feat.index[idx]
    test_mask = np.asarray(sig_ts >= train_end_ts)
    train_idx, train_dir = idx[~test_mask], direction[~test_mask]
    test_idx, test_dir = idx[test_mask], direction[test_mask]
    train_trades = run_backtest(feat, train_idx, train_dir, arrays=arrays, **exit_params)
    test_trades = run_backtest(feat, test_idx, test_dir, arrays=arrays, **exit_params)
    train_metrics = compute_metrics(train_trades)
    test_metrics = compute_metrics(test_trades)

    outlier_check = outlier_dominance_check(full_trades, n_drop=2) if len(full_trades) > 2 else {}

    baseline_results = None
    baseline_summary = None
    if full_metrics.get("n_trades", 0) >= MIN_TRADES:
        lf = long_frac_of(full_trades["direction"].to_numpy())
        baseline_results = random_baseline(
            feat, n_trades=full_metrics["n_trades"], long_frac=lf,
            stop_mult=exit_params["stop_mult"], target_mult=exit_params["target_mult"],
            max_hold=exit_params["max_hold"], fee_bps=exit_params["fee_bps"], slip_bps=exit_params["slip_bps"],
            n_sims=n_random_sims, arrays=arrays)
        rets = [r.get("net_return_total", np.nan) for r in baseline_results if r.get("n_trades", 0) > 0]
        pfs = [r.get("profit_factor") for r in baseline_results if r.get("profit_factor") is not None]
        strat_ret = full_metrics.get("net_return_total", np.nan)
        pct_rank = float(np.mean([strat_ret > r for r in rets])) if rets else None
        baseline_summary = {
            "n_sims": len(baseline_results),
            "random_mean_return": float(np.mean(rets)) if rets else None,
            "random_median_return": float(np.median(rets)) if rets else None,
            "random_std_return": float(np.std(rets)) if rets else None,
            "random_mean_pf": float(np.mean(pfs)) if pfs else None,
            "strategy_return_percentile_vs_random": pct_rank,
            "beats_random_mean": bool(strat_ret > np.mean(rets)) if rets else None,
        }

    robustness = []
    if param_grid:
        for params in param_grid:
            try:
                idx_p, dir_p = fn(feat, **params)
            except TypeError:
                continue
            tr_p = run_backtest(feat, idx_p, dir_p, arrays=arrays, **exit_params)
            m_p = compute_metrics(tr_p)
            robustness.append({"params": params, "n_trades": m_p.get("n_trades"),
                                "net_return_total": m_p.get("net_return_total"),
                                "profit_factor": m_p.get("profit_factor")})

    # ---- rejection criteria ----
    reasons = []
    n_trades = full_metrics.get("n_trades", 0)
    if n_trades < MIN_TRADES:
        reasons.append(f"fewer than {MIN_TRADES} trades ({n_trades})")
    pf = full_metrics.get("profit_factor")
    if n_trades >= MIN_TRADES and (pf is None or pf <= 1.0):
        reasons.append(f"profit factor <= 1 after fees/slippage ({pf})")
    if n_trades >= MIN_TRADES and baseline_summary and baseline_summary.get("beats_random_mean") is False:
        reasons.append("does not beat random-entry baseline with identical exit rule/costs")
    if outlier_check.get("outlier_dominated"):
        reasons.append("result is dominated by top-2 trades (fails after removing them)")
    if robustness:
        signs = [1 if (r["net_return_total"] or 0) > 0 else -1 for r in robustness if r["net_return_total"] is not None]
        if signs and (sum(s == 1 for s in signs) / len(signs)) < 0.6:
            reasons.append("not robust to nearby parameter choices (return sign flips across grid)")

    verdict = "ACCEPT" if not reasons else "REJECT"

    result = {
        "strategy": name,
        "n_signals_raised": int(n_total_signals),
        "exit_params": exit_params,
        "full_sample": full_metrics,
        "train_sample": train_metrics,
        "test_sample_oos": test_metrics,
        "outlier_dominance_check": outlier_check,
        "random_baseline": baseline_summary,
        "robustness_grid": robustness,
        "verdict": verdict,
        "rejection_reasons": reasons,
        "runtime_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(RESULTS, f"{name}.json"), "w") as f:
        json.dump(result, f, indent=2, default=jsonable)
    print(f"[{name}] signals={n_total_signals} trades={n_trades} "
          f"net_ret={full_metrics.get('net_return_total')} pf={pf} verdict={verdict} "
          f"({result['runtime_sec']}s)")
    if reasons:
        for r in reasons:
            print(f"    - {r}")
    return result


PARAM_GRIDS = {
    "S1_momentum": [
        dict(vol_z_th=v, ret_z_th=r, body_ratio_th=b)
        for v in [1.7, 2.0, 2.3] for r in [1.7, 2.0, 2.3] for b in [0.5, 0.6, 0.7]
    ],
    "S2_imbalance": [
        dict(taker_z_th=t, vol_z_th=v)
        for t in [2.0, 2.5, 3.0] for v in [0.7, 1.0, 1.3]
    ],
    "S3_oi": [
        dict(vol_z_th=v, ret_z_th=r, oi_z_th=o)
        for v in [1.7, 2.0, 2.3] for r in [1.7, 2.0, 2.3] for o in [-1.2, -1.5, -1.8]
    ],
    "S4_volatility": [
        dict(vol_z_th=v, ret_z_th=r, range_z_th=g)
        for v in [2.2, 2.5, 2.8] for r in [1.2, 1.5, 1.8] for g in [1.7, 2.0, 2.3]
    ],
    "S5_levels": [
        dict(vol_z_th=v, ret_z_th=r)
        for v in [1.7, 2.0, 2.3] for r in [1.7, 2.0, 2.3]
    ],
}


if __name__ == "__main__":
    print("Loading base data + engineering features...")
    base = load_base()
    feat = engineer_features(base)
    arrays = make_arrays(feat)
    train_end_ts = chronological_split(feat, train_frac=0.7)
    print(f"Data range: {feat.index.min()} -> {feat.index.max()}, n={len(feat)}")
    print(f"Chronological 70/30 split at: {train_end_ts}")

    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    targets = STRATEGIES.items() if which == "all" else [(which, STRATEGIES[which])]

    all_results = {}
    for name, fn in targets:
        grid = PARAM_GRIDS.get(name)
        res = run_one(name, fn, feat, arrays, train_end_ts, param_grid=grid)
        all_results[name] = res

    print("\n=== SUMMARY ===")
    for name, res in all_results.items():
        print(name, "->", res["verdict"], res["rejection_reasons"])
