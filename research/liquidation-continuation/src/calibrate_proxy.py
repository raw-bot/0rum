"""Validate the proxy liquidation-spike detector against REAL liquidation orders.

Real liquidation tape is only available for BTCUSD_PERP (COIN-M) over
2023-06-25 -> 2024-10-14 (Binance Data Vision; see DATA_NOTES.md). We compute
the same proxy features on BTCUSD_PERP's own 5m klines (not BTCUSDT) over the
matching window, then check whether proxy spikes coincide in time and
direction with real liquidation flow. This calibrates the proxy methodology
before it is applied to BTCUSDT (where no real liquidation tape exists).
"""
import os
import numpy as np
import pandas as pd
from features import load_base, engineer_features, PROC

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
os.makedirs(OUT, exist_ok=True)


def main():
    px = load_base(symbol_prefix="liq_real_", premium=False)
    feat = engineer_features(px, vol_window=288, ret_window=288)

    liq = pd.read_parquet(os.path.join(PROC, "liq_real.parquet")).set_index("ts").sort_index()
    liq["usd"] = liq["orig_qty"] * liq["price"]
    # side=SELL -> liquidation engine force-SOLD -> closed a LONG position ("long liq")
    # side=BUY  -> liquidation engine force-BOUGHT -> closed a SHORT position ("short liq")
    liq["long_liq_usd"] = np.where(liq["side"] == "SELL", liq["usd"], 0.0)
    liq["short_liq_usd"] = np.where(liq["side"] == "BUY", liq["usd"], 0.0)

    bucket = liq.resample("5min").agg(
        long_liq_usd=("long_liq_usd", "sum"),
        short_liq_usd=("short_liq_usd", "sum"),
        n_orders=("usd", "size"),
    )
    bucket["total_liq_usd"] = bucket["long_liq_usd"] + bucket["short_liq_usd"]
    bucket["net_dir"] = np.sign(bucket["short_liq_usd"] - bucket["long_liq_usd"])  # +1 if more shorts liquidated (price up pressure)

    df = feat.join(bucket, how="inner")
    df["total_liq_usd"] = df["total_liq_usd"].fillna(0.0)
    df = df.dropna(subset=["vol_z", "ret_z"])

    print(f"Calibration window: {df.index.min()} -> {df.index.max()}, {len(df)} bars")
    print(f"Real liquidation $ total: {bucket['total_liq_usd'].sum():,.0f}, "
          f"bars with any real liq: {(bucket['total_liq_usd']>0).mean()*100:.1f}%")

    # 1) Rank correlation: proxy_intensity vs real liquidation $ in the same bar
    corr = df["proxy_intensity"].corr(df["total_liq_usd"], method="spearman")
    print(f"\nSpearman corr(proxy_intensity, real_total_liq_usd) = {corr:.3f}")

    # 2) Lift: are proxy_spike bars enriched for real liquidation activity vs base rate?
    base_rate_any_liq = (df["total_liq_usd"] > 0).mean()
    spike_rate_any_liq = (df.loc[df["proxy_spike"], "total_liq_usd"] > 0).mean()
    print(f"P(real liq present | bar) = {base_rate_any_liq*100:.1f}%")
    print(f"P(real liq present | proxy_spike) = {spike_rate_any_liq*100:.1f}%  "
          f"(lift = {spike_rate_any_liq/base_rate_any_liq:.2f}x)")

    base_mean_liq = df["total_liq_usd"].mean()
    spike_mean_liq = df.loc[df["proxy_spike"], "total_liq_usd"].mean()
    print(f"E[real_liq_usd | bar] = {base_mean_liq:,.0f}")
    print(f"E[real_liq_usd | proxy_spike] = {spike_mean_liq:,.0f}  (lift = {spike_mean_liq/base_mean_liq:.2f}x)")

    # 3) Direction agreement: among bars with meaningful real liquidation flow,
    #    does proxy_dir agree with which side (long/short) was liquidated more?
    sig = df[df["total_liq_usd"] > df["total_liq_usd"].quantile(0.90)].copy()
    sig["proxy_dir_sign"] = np.sign(sig["ret"])
    agree = (np.sign(sig["proxy_dir_sign"]) == sig["net_dir"]).mean()
    print(f"\nAmong top-decile real-liquidation bars (n={len(sig)}): "
          f"proxy candle direction agrees with net real liquidation side {agree*100:.1f}% of the time "
          f"(50% = chance)")

    summary = {
        "window_start": str(df.index.min()), "window_end": str(df.index.max()),
        "n_bars": int(len(df)),
        "spearman_corr_intensity_vs_real_liq": float(corr),
        "base_rate_any_real_liq": float(base_rate_any_liq),
        "spike_rate_any_real_liq": float(spike_rate_any_liq),
        "lift_presence": float(spike_rate_any_liq / base_rate_any_liq),
        "lift_magnitude": float(spike_mean_liq / base_mean_liq),
        "direction_agreement_top_decile": float(agree),
    }
    import json
    with open(os.path.join(OUT, "proxy_calibration.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("\nSaved results/proxy_calibration.json")


if __name__ == "__main__":
    main()
