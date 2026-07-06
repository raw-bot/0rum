"""Consolidate raw daily/monthly Binance Data Vision zips into clean parquet files.

Outputs (in data/processed/):
  klines_5m.parquet        BTCUSDT 5m OHLCV + taker buy volume, USDT-M
  metrics_5m.parquet       BTCUSDT OI / top-trader ratio / taker L-S vol ratio, 5m native
  funding.parquet          BTCUSDT funding rate events (~8h)
  liq_real.parquet         BTCUSD_PERP real liquidation orders (COIN-M, 2023-06-25 to 2024-10-14)
  liq_real_klines_5m.parquet  BTCUSD_PERP 5m OHLCV for the calibration window
"""
import glob
import os
import zipfile
import io
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "processed")
os.makedirs(OUT, exist_ok=True)

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "trades", "taker_buy_vol", "taker_buy_quote_vol", "ignore"]


def read_all_csv_from_zips(folder, pattern="*.zip", header="infer", names=None):
    files = sorted(glob.glob(os.path.join(folder, pattern)))
    frames = []
    for fp in files:
        try:
            with zipfile.ZipFile(fp) as z:
                for n in z.namelist():
                    if not n.endswith(".csv"):
                        continue
                    raw = z.read(n)
                    if not raw.strip():
                        continue
                    # some files have a literal header row repeated as first data row;
                    # detect by checking if first bytes are non-numeric
                    first_line = raw.split(b"\n", 1)[0].decode(errors="ignore")
                    has_header = first_line.split(",")[0].strip().lower() in (
                        "open_time", "create_time", "calc_time", "time")
                    df = pd.read_csv(io.BytesIO(raw),
                                      header=0 if has_header else None,
                                      names=None if has_header else names)
                    # Binance has changed header column names across eras (e.g.
                    # taker_buy_vol vs taker_buy_volume) while keeping column order
                    # fixed -- force positional names so all eras concatenate cleanly.
                    if names is not None and len(df.columns) == len(names):
                        df.columns = names
                    frames.append(df)
        except zipfile.BadZipFile:
            print(f"WARN: bad zip {fp}, skipping")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_klines(folder, out_name):
    df = read_all_csv_from_zips(folder, names=KLINE_COLS)
    if df.empty:
        print(f"{out_name}: NO DATA")
        return
    # open_time may be ms or us depending on era; Binance switched some endpoints to us in 2025
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    # normalize to ms: if value too large for ms-since-1970 in a sane range, assume us
    median = df["open_time"].median()
    if median > 4e15:  # microseconds
        df["open_time"] = (df["open_time"] / 1000).astype("int64")
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_vol", "taker_buy_quote_vol"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce")
    df = df.dropna(subset=["ts", "open", "high", "low", "close"])
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    keep = ["ts", "open", "high", "low", "close", "volume", "quote_volume", "trades",
            "taker_buy_vol", "taker_buy_quote_vol"]
    df = df[keep]
    dest = os.path.join(OUT, out_name)
    df.to_parquet(dest)
    print(f"{out_name}: {len(df)} rows, {df['ts'].min()} -> {df['ts'].max()}")


def build_metrics():
    folder = os.path.join(RAW, "metrics")
    df = read_all_csv_from_zips(folder)
    if df.empty:
        print("metrics: NO DATA")
        return
    df["ts"] = pd.to_datetime(df["create_time"], utc=True, format="mixed")
    for c in ["sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio",
              "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ts"]).drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    keep = ["ts", "sum_open_interest", "sum_open_interest_value", "sum_toptrader_long_short_ratio",
            "sum_taker_long_short_vol_ratio"]
    df = df[keep]
    dest = os.path.join(OUT, "metrics_5m.parquet")
    df.to_parquet(dest)
    print(f"metrics_5m: {len(df)} rows, {df['ts'].min()} -> {df['ts'].max()}")


def build_funding():
    folder = os.path.join(RAW, "funding")
    df = read_all_csv_from_zips(folder)
    if df.empty:
        print("funding: NO DATA")
        return
    df["calc_time"] = pd.to_numeric(df["calc_time"], errors="coerce")
    df["ts"] = pd.to_datetime(df["calc_time"], unit="ms", utc=True)
    df["last_funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
    df = df.dropna(subset=["ts"]).drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    df = df[["ts", "last_funding_rate"]]
    dest = os.path.join(OUT, "funding.parquet")
    df.to_parquet(dest)
    print(f"funding: {len(df)} rows, {df['ts'].min()} -> {df['ts'].max()}")


def build_liq_real():
    # CSV header: time,side,order_type,time_in_force,original_quantity,price,
    #             average_price,order_status,last_fill_quantity,accumulated_fill_quantity
    folder = os.path.join(RAW, "liq_real_btcusd_perp")
    df = read_all_csv_from_zips(folder)
    if df.empty:
        print("liq_real: NO DATA")
        return
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    df["ts"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    for c in ["original_quantity", "price", "average_price", "last_fill_quantity", "accumulated_fill_quantity"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    # Binance's snapshot feed repeats each liquidation order line (observed duplicate
    # consecutive rows in every daily file) -- de-dup on the full record.
    df = df.drop_duplicates(subset=["time", "side", "price", "original_quantity",
                                     "accumulated_fill_quantity"])
    keep = ["ts", "side", "original_quantity", "price", "average_price", "accumulated_fill_quantity"]
    df = df[keep].rename(columns={"original_quantity": "orig_qty", "average_price": "avg_price",
                                   "accumulated_fill_quantity": "filled_qty"})
    dest = os.path.join(OUT, "liq_real.parquet")
    df.to_parquet(dest)
    print(f"liq_real: {len(df)} rows, {df['ts'].min()} -> {df['ts'].max()}")


if __name__ == "__main__":
    build_klines(os.path.join(RAW, "klines_5m"), "klines_5m.parquet")
    build_metrics()
    build_funding()
    build_liq_real()
    build_klines(os.path.join(RAW, "liq_real_klines"), "liq_real_klines_5m.parquet")
