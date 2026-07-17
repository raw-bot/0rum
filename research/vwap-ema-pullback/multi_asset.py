"""Porte 1 — validation multi-actifs SANS re-tuning (anti-overfit rule n°1).

Fetches 4h monthly klines from data.binance.vision for BTC, ETH (crypto family)
and PAXG (spot gold — the DECORRELATED asset), then runs the EXACT SAME signal
and params as run.py on each, with NO per-asset re-tuning. Rule (PLAN_BACKTESTS.md):
a strategy is retained only if profitable on >=2 decorrelated assets without retuning.
BTC+ETH are correlated; PAXG(gold) is the decorrelation test.
"""
import io
import os
import sys
import zipfile
import datetime as dt
import urllib.request
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "liquidation-continuation", "src"))
from run import signal_vwap_ema_pullback, EXIT  # noqa: E402
from backtest import run_backtest, compute_metrics, random_baseline, make_arrays  # noqa: E402

BASE = "https://data.binance.vision/data"
RAW = os.path.join(HERE, "data_4h")
os.makedirs(RAW, exist_ok=True)

# (symbol, market_segment, path_market) — um futures for crypto, spot for PAXG gold
ASSETS = [
    ("BTCUSDT", "futures/um", "BTC (crypto)"),
    ("ETHUSDT", "futures/um", "ETH (crypto)"),
    ("PAXGUSDT", "spot", "PAXG/gold (DECORRELATED)"),
]
START = (2021, 1)
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore"]


def months(start_ym, end_ym):
    y, m = start_ym
    while (y, m) <= end_ym:
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def fetch_zip(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "cached"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return "ok"
    except urllib.error.HTTPError as e:
        return "missing" if e.code == 404 else "failed"
    except Exception:
        return "failed"


def load_asset_4h(symbol, seg):
    today = dt.date.today()
    frames = []
    for y, m in months(START, (today.year, today.month)):
        fname = f"{symbol}-4h-{y:04d}-{m:02d}.zip"
        url = f"{BASE}/{seg}/monthly/klines/{symbol}/4h/{fname}"
        dest = os.path.join(RAW, fname)
        st = fetch_zip(url, dest)
        if st in ("missing", "failed"):
            continue
        with zipfile.ZipFile(dest) as z:
            raw = z.read(z.namelist()[0]).decode()
        # newer dumps ship a header row; detect & skip it
        first = raw.split("\n", 1)[0].split(",")[0]
        header = 0 if first.replace(".", "").isdigit() else "infer"
        df = pd.read_csv(io.StringIO(raw), header=header, names=KLINE_COLS if header == 0 else None)
        frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    # open_time unit is mixed within one asset: Binance switched klines from ms to
    # microseconds mid-2025, so old months are ms and recent months are us. Normalise
    # PER ROW: a 2021 ms epoch ~1.6e12, a 2025 us epoch ~1.6e15 -> 1e14 cleanly splits.
    ot = d["open_time"].astype("int64")
    ms = pd.to_datetime(ot.where(ot < 10**14), unit="ms", utc=True)
    us = pd.to_datetime(ot.where(ot >= 10**14), unit="us", utc=True)
    d["ts"] = ms.fillna(us)
    d = d.set_index("ts").sort_index()
    d = d[~d.index.duplicated(keep="last")]
    df = d[["open", "high", "low", "close", "volume"]].astype(float)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14, min_periods=5).mean() / df["close"]
    return df


def run_asset(label, df, ema_len, vwap_len):
    arrays = make_arrays(df)
    idx, d = signal_vwap_ema_pullback(df, ema_len=ema_len, vwap_len=vwap_len)
    m = compute_metrics(run_backtest(df, idx, d, arrays=arrays, **EXIT))
    if m.get("n_trades", 0) < 20:
        return m, None
    rnd = random_baseline(df, m["n_trades"], 1.0, EXIT["stop_mult"], EXIT["target_mult"],
                          EXIT["max_hold"], EXIT["fee_bps"], EXIT["slip_bps"], n_sims=120, arrays=arrays)
    beat = float((np.array([r.get("expectancy_r_multiple", 0.0) for r in rnd]) < m["expectancy_r_multiple"]).mean())
    return m, beat


def daily_returns(df):
    return df["close"].resample("1D").last().pct_change().dropna()


def main():
    dfs = {}
    print("loading 4h data (fetch on first run)...")
    for sym, seg, label in ASSETS:
        df = load_asset_4h(sym, seg)
        dfs[label] = df
        print(f"  {label:26s} {len(df):>6} bars  {df.index[0].date()} -> {df.index[-1].date()}")

    # correlation of daily returns over the common window (sanity: is PAXG really decorrelated?)
    rets = pd.DataFrame({lbl: daily_returns(df) for lbl, df in dfs.items()}).dropna()
    print("\ndaily-return correlation (common window):")
    print(rets.corr().round(2).to_string())

    for ema_len, vwap_len, tag in [(9, 50, "default (run.py)"), (9, 200, "grid winner: slow anchor")]:
        print(f"\n================ SAME PARAMS ema{ema_len}/vwap{vwap_len} — {tag} ================")
        print(f"{'asset':26s} | {'n':>4} {'net':>8} {'PF':>5} {'win':>4} {'expR':>7} {'maxDD':>6} {'>rand':>6}")
        for _, _, label in ASSETS:
            m, beat = run_asset(label, dfs[label], ema_len, vwap_len)
            if not m or m.get("n_trades", 0) < 20:
                print(f"{label:26s} |  (too few trades: {m.get('n_trades', 0)})")
                continue
            pf = ("%.2f" % m["profit_factor"]) if m.get("profit_factor") else "inf"
            print(f"{label:26s} | {m['n_trades']:>4} {m['net_return_total']:>+7.1%} {pf:>5} "
                  f"{m['win_rate']:>3.0%} {m['expectancy_r_multiple']:>+7.3f} {m['max_drawdown']:>5.0%} "
                  f"{beat:>5.0%}")


if __name__ == "__main__":
    main()
