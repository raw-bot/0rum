"""Load consolidated data and engineer proxy liquidation features.

All proxy features are built ONLY from information available at the close of
bar t (klines, OI/taker-ratio metrics resampled to the bar, funding rate known
as of t, premium index at t). Signals derived here are used to enter at the
OPEN of bar t+1 in backtest.py -- never at bar t's own close/high/low -- so
there is no lookahead.

Proxy liquidation logic (clearly labeled as a proxy, real liquidation tape is
not available for USDT-margined BTCUSDT -- see DATA_NOTES.md):
  - abnormal volume (rolling z-score)
  - abnormal |return| / range (rolling z-score) i.e. a large directional candle
  - concurrent open-interest contraction (forced deleveraging signature, as
    opposed to OI expansion which indicates fresh positioning, not closing)
  - real taker buy/sell volume ratio (sum_taker_long_short_vol_ratio) as an
    actual-data proxy for aggressive one-sided (liquidation-style) order flow
  - real funding rate extremes (crowded positioning before a flush)
  - real mark/index premium extremes (basis dislocation around stress events)
"""
import os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, "data", "processed")


def zscore(s, window, min_periods=None):
    min_periods = min_periods or max(10, window // 4)
    m = s.rolling(window, min_periods=min_periods).mean()
    sd = s.rolling(window, min_periods=min_periods).std()
    return (s - m) / sd.replace(0, np.nan)


def load_base(symbol_prefix="", premium=True):
    """symbol_prefix: '' for main BTCUSDT dataset, 'liq_real_' for the COIN-M
    calibration window (BTCUSD_PERP klines only, no metrics/funding for that symbol)."""
    klines = pd.read_parquet(os.path.join(PROC, f"{symbol_prefix}klines_5m.parquet"))
    klines = klines.set_index("ts").sort_index()
    df = klines.copy()

    if symbol_prefix == "":
        metrics = pd.read_parquet(os.path.join(PROC, "metrics_5m.parquet")).set_index("ts").sort_index()
        metrics = metrics[~metrics.index.duplicated(keep="last")]
        df = df.join(metrics, how="left")
        df[["sum_open_interest", "sum_open_interest_value", "sum_toptrader_long_short_ratio",
            "sum_taker_long_short_vol_ratio"]] = df[
            ["sum_open_interest", "sum_open_interest_value", "sum_toptrader_long_short_ratio",
             "sum_taker_long_short_vol_ratio"]].ffill(limit=12)

        funding = pd.read_parquet(os.path.join(PROC, "funding.parquet")).set_index("ts").sort_index()
        funding = funding[~funding.index.duplicated(keep="last")]
        funding_aligned = funding.reindex(df.index.union(funding.index)).sort_index()
        funding_aligned["last_funding_rate"] = funding_aligned["last_funding_rate"].ffill()
        df["funding_rate"] = funding_aligned.reindex(df.index)["last_funding_rate"]

        if premium and os.path.exists(os.path.join(PROC, "premium_5m.parquet")):
            prem = pd.read_parquet(os.path.join(PROC, "premium_5m.parquet")).set_index("ts").sort_index()
            df["premium"] = prem["close"].reindex(df.index).ffill(limit=3)

    return df


def engineer_features(df, vol_window=288, ret_window=288, oi_window=288,
                       funding_window=270, premium_window=288,
                       spike_vol_z=2.0, spike_ret_z=2.0):
    """vol_window/ret_window default 288 bars = 24h on 5m data."""
    out = df.copy()
    out["ret"] = out["close"].pct_change()
    out["abs_ret"] = out["ret"].abs()
    out["range_pct"] = (out["high"] - out["low"]) / out["close"].shift(1)
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr_pct"] = (tr.rolling(14, min_periods=5).mean()) / out["close"]

    out["vol_z"] = zscore(out["volume"], vol_window)
    out["ret_z"] = zscore(out["abs_ret"], ret_window)
    out["range_z"] = zscore(out["range_pct"], ret_window)

    if "sum_open_interest" in out.columns:
        out["oi_chg"] = out["sum_open_interest"].diff()
        out["oi_chg_pct"] = out["sum_open_interest"].pct_change()
        out["oi_chg_z"] = zscore(out["oi_chg_pct"], oi_window)
        out["taker_ls_ratio"] = out["sum_taker_long_short_vol_ratio"]
        out["taker_ls_z"] = zscore(np.log(out["taker_ls_ratio"].clip(lower=1e-3)), oi_window)

    if "funding_rate" in out.columns:
        out["funding_z"] = zscore(out["funding_rate"], funding_window)

    if "premium" in out.columns:
        out["premium_z"] = zscore(out["premium"], premium_window)

    # --- proxy liquidation spike: abnormal volume AND abnormal directional move
    out["proxy_spike"] = (out["vol_z"] > spike_vol_z) & (out["ret_z"] > spike_ret_z)
    out["proxy_intensity"] = out["vol_z"].clip(lower=0) * out["ret_z"].clip(lower=0)
    out["proxy_dir"] = np.sign(out["ret"])  # +1 = up-spike (proxy short-liq), -1 = down-spike (proxy long-liq)

    # liquidity zones: rolling N-bar high/low (stops/liq clusters tend to sit just beyond these)
    for n, label in [(288, "1d"), (96, "8h"), (24, "2h")]:
        out[f"hh_{label}"] = out["high"].rolling(n, min_periods=n // 2).max()
        out[f"ll_{label}"] = out["low"].rolling(n, min_periods=n // 2).min()

    return out


if __name__ == "__main__":
    df = load_base()
    feat = engineer_features(df)
    print(feat.shape)
    print(feat[["close", "vol_z", "ret_z", "oi_chg_z", "taker_ls_z", "funding_z", "premium_z",
                "proxy_spike"]].dropna().tail(10))
    print("proxy_spike rate:", feat["proxy_spike"].mean())
    n_spike_per_q = feat.dropna(subset=["vol_z", "ret_z"]).assign(
        q=lambda d: d.index.to_period("Q")).groupby("q")["proxy_spike"].sum()
    print(n_spike_per_q)
