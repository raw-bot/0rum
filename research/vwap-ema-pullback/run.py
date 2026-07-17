"""VWAP-EMA pullback — reframed testable version of the @austin.daytrades reel.

The reel taught a DISCRETIONARY 3-min index-options scalp: buy on a candle that
wicks/rejects VWAP-or-EMA9 in the direction of the VWAP trend. That is not
codeable as taught (no stop, no TP, "wick or reject" / "trend continuous"
undefined — all flagged "non déterminé" in the watch report). Here we keep only
the transferable IDEA and rebuild it as a falsifiable, crypto/4h, LONG-ONLY
signal, then judge it with 0rum's own harness and rules:

  IDEA:  enter in the direction of a volume-weighted trend anchor, on a pullback
         that wicks a fast EMA and reclaims it.
  CRYPTO ADAPTATION (24/7, no RTH session):
         session-VWAP is meaningless intraday on crypto, so the trend anchor is a
         ROLLING volume-weighted price (rolling VWAP over `vwap_len` bars). The
         EMA9 of the reel becomes a parametrised fast EMA on the 4h frame.
  0rum CONSTRAINT: long-only (mirrors orum/external/ak_macd.py allow_short=false).

Evaluation (per research/PLAN_BACKTESTS.md):
  - costs always in (fee_bps=5 side, slip_bps=3 — 0rum default exit params)
  - benchmarks: buy & hold + Donchian-20 breakout (same exit engine, apples-to-apples)
  - anti-overfit: chronological OOS split, random-entry baseline, outlier-dominance,
    and a FULL parameter grid printed (no cherry-pick).
  - a candidate that doesn't beat Donchian doesn't pay its complexity.

HONEST SCOPE: BTC-only first cut. The multi-asset rule (profitable on >=2
decorrelated assets WITHOUT per-asset re-tuning) can't be checked here — ETH/PAXG
4h data isn't in this project's processed/ yet. This run decides only whether the
idea is worth promoting to that multi-asset gate.
"""
import os
import sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_SRC = os.path.join(HERE, "..", "liquidation-continuation", "src")
KLINES_5M = os.path.join(HERE, "..", "liquidation-continuation", "data", "processed", "klines_5m.parquet")
sys.path.insert(0, ENGINE_SRC)

from backtest import (run_backtest, compute_metrics, outlier_dominance_check,  # noqa: E402
                      random_baseline, make_arrays)

# 0rum's standard exit / costs (run_strategy.DEFAULT_EXIT). max_hold scaled for 4h:
# 96 bars was 8h on 5m; keep the same 96-bar cap here -> 16 days on 4h, a loose timeout.
EXIT = dict(stop_mult=1.5, target_mult=2.0, max_hold=96, fee_bps=5.0, slip_bps=3.0)
MIN_TRADES = 40
TRAIN_FRAC = 0.70


def load_btc_4h():
    k = pd.read_parquet(KLINES_5M).set_index("ts").sort_index()
    o = k["open"].resample("4h").first()
    h = k["high"].resample("4h").max()
    l = k["low"].resample("4h").min()
    c = k["close"].resample("4h").last()
    v = k["volume"].resample("4h").sum()
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()
    # ATR% (same construction as features.py), known as of bar close.
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14, min_periods=5).mean() / df["close"]
    return df


def rolling_vwap(df, n):
    pv = ((df["high"] + df["low"] + df["close"]) / 3.0) * df["volume"]
    return pv.rolling(n, min_periods=max(5, n // 4)).sum() / df["volume"].rolling(n, min_periods=max(5, n // 4)).sum()


def signal_vwap_ema_pullback(df, ema_len=9, vwap_len=50):
    """Long-only. All conditions use bar i's close/high/low/volume, entry at i+1 open.

    Trend up  : close > rolling-VWAP AND rolling-VWAP rising.
    Pullback  : bar wicks the fast EMA (low <= ema) but reclaims it (close > ema).
    """
    ema = df["close"].ewm(span=ema_len, adjust=False).mean()
    rv = rolling_vwap(df, vwap_len)
    trend_up = (df["close"] > rv) & (rv > rv.shift(1))
    pullback = (df["low"] <= ema) & (df["close"] > ema)
    cond = (trend_up & pullback).fillna(False)
    idx = np.where(cond.to_numpy())[0]
    return idx, np.ones(len(idx), dtype=int)


def donchian_long(df, n=20):
    """Benchmark: long-only breakout of the prior n-bar high (classic trend baseline)."""
    hh = df["high"].rolling(n).max().shift(1)
    cond = (df["close"] > hh).fillna(False)
    idx = np.where(cond.to_numpy())[0]
    return idx, np.ones(len(idx), dtype=int)


def buy_and_hold(df):
    r = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    fee = 2 * EXIT["fee_bps"] / 10000.0
    return r - fee


def fmt(m):
    if not m or m.get("n_trades", 0) == 0:
        return "  (no trades)"
    return ("  n={n_trades}  net={net:+.1%}  PF={pf}  win={win:.0%}  "
            "exp_R={er:+.3f}  maxDD={dd:.0%}  avg_bars={ab:.0f}").format(
        n_trades=m["n_trades"], net=m["net_return_total"],
        pf=("%.2f" % m["profit_factor"]) if m.get("profit_factor") else "inf",
        win=m["win_rate"], er=m["expectancy_r_multiple"], dd=m["max_drawdown"],
        ab=m["avg_bars_held"])


def evaluate(name, idx, direction, df, arrays, train_end_ts):
    print(f"\n=== {name} ===")
    tr = run_backtest(df, idx, direction, arrays=arrays, **EXIT)
    m = compute_metrics(tr)
    print(" full :" + fmt(m))
    if m.get("n_trades", 0) < MIN_TRADES:
        print(f"  -> below MIN_TRADES ({MIN_TRADES}); not enough evidence.")
        return m, tr
    sig_ts = df.index[idx]
    te = np.asarray(sig_ts >= train_end_ts)
    m_tr = compute_metrics(run_backtest(df, idx[~te], direction[~te], arrays=arrays, **EXIT))
    m_oos = compute_metrics(run_backtest(df, idx[te], direction[te], arrays=arrays, **EXIT))
    print(" train:" + fmt(m_tr))
    print(" OOS  :" + fmt(m_oos))
    od = outlier_dominance_check(tr, n_drop=2)
    print(f"  outlier-dominance (drop top 2): PF={od.get('pf_after_dropping_top_2')}, "
          f"dominated={od.get('outlier_dominated')}")
    long_frac = 1.0
    rnd = random_baseline(df, m["n_trades"], long_frac, EXIT["stop_mult"], EXIT["target_mult"],
                          EXIT["max_hold"], EXIT["fee_bps"], EXIT["slip_bps"], n_sims=150, arrays=arrays)
    rnd_er = np.array([r.get("expectancy_r_multiple", 0.0) for r in rnd])
    pct = float((rnd_er < m["expectancy_r_multiple"]).mean())
    print(f"  random-entry baseline: strat exp_R={m['expectancy_r_multiple']:+.3f} vs "
          f"random median={np.median(rnd_er):+.3f}  (strat beats {pct:.0%} of random sims)")
    return m, tr


def main():
    df = load_btc_4h()
    arrays = make_arrays(df)
    n = len(df)
    train_end_ts = df.index[int(n * TRAIN_FRAC)]
    print(f"BTC 4h: {n} bars  {df.index[0]} -> {df.index[-1]}")
    print(f"train/OOS split at {train_end_ts}  ({TRAIN_FRAC:.0%} train)")
    print(f"buy & hold over window (net of 1 RT): {buy_and_hold(df):+.1%}")

    idx, d = signal_vwap_ema_pullback(df)
    evaluate("VWAP-EMA pullback (ema9 / vwap50, long-only)", idx, d, df, arrays, train_end_ts)

    bidx, bd = donchian_long(df, 20)
    evaluate("BENCHMARK Donchian-20 breakout (long-only)", bidx, bd, df, arrays, train_end_ts)

    # Parameter robustness — print the WHOLE grid, no cherry-pick.
    print("\n=== parameter grid (full sample; no cherry-pick) ===")
    print(f"{'ema':>4} {'vwap':>5} | {'n':>4} {'net':>8} {'PF':>5} {'win':>5} {'expR':>7} {'maxDD':>6}")
    for ema_len in (7, 9, 13, 21):
        for vwap_len in (30, 50, 100, 200):
            i2, d2 = signal_vwap_ema_pullback(df, ema_len=ema_len, vwap_len=vwap_len)
            m2 = compute_metrics(run_backtest(df, i2, d2, arrays=arrays, **EXIT))
            if m2.get("n_trades", 0) == 0:
                print(f"{ema_len:>4} {vwap_len:>5} |  (no trades)")
                continue
            pf = ("%.2f" % m2["profit_factor"]) if m2.get("profit_factor") else "inf"
            print(f"{ema_len:>4} {vwap_len:>5} | {m2['n_trades']:>4} {m2['net_return_total']:>+7.1%} "
                  f"{pf:>5} {m2['win_rate']:>4.0%} {m2['expectancy_r_multiple']:>+7.3f} {m2['max_drawdown']:>5.0%}")


if __name__ == "__main__":
    main()
