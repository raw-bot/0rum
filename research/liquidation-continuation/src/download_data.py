"""Download BTCUSDT (USDT-M) klines/metrics/funding and BTCUSD_PERP (COIN-M)
liquidationSnapshot from Binance's official public data repo (data.binance.vision).

This is the documented public dataset (https://github.com/binance/binance-public-data/)
served from an S3 bucket. No API key required, no rate limiting beyond plain HTTP.
"""
import io
import os
import sys
import time
import zipfile
import datetime as dt
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://data.binance.vision/data"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def fetch(url, dest, retries=3):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return "cached"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "missing"
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return "failed"


def _one(args):
    market, category, symbol, subdir, ds, outdir = args
    # klines/markPriceKlines/etc use the interval (subdir) as the filename's
    # middle field, not the category name, e.g. BTCUSDT-5m-2020-09-01.zip
    middle = subdir if subdir else category
    fname = f"{symbol}-{middle}-{ds}.zip"
    if subdir:
        url = f"{BASE}/futures/{market}/daily/{category}/{symbol}/{subdir}/{fname}"
    else:
        url = f"{BASE}/futures/{market}/daily/{category}/{symbol}/{fname}"
    dest = os.path.join(outdir, fname)
    return fetch(url, dest)


def download_daily(category, symbol, start, end, market="um", subdir=None, label=None, workers=16):
    label = label or category
    outdir = os.path.join(RAW, label)
    os.makedirs(outdir, exist_ok=True)
    jobs = [(market, category, symbol, subdir, d.strftime("%Y-%m-%d"), outdir) for d in daterange(start, end)]
    counts = {"ok": 0, "missing": 0, "cached": 0, "failed": 0}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, j): j for j in jobs}
        for fut in as_completed(futs):
            status = fut.result()
            counts[status if status in counts else "failed"] += 1
            done += 1
            if done % 200 == 0:
                print(f"  [{label}] {done}/{len(jobs)} {counts}", flush=True)
    print(f"[{label}] DONE {counts}", flush=True)


def download_monthly(category, symbol, start_ym, end_ym, market="um", label=None):
    label = label or category
    outdir = os.path.join(RAW, label)
    os.makedirs(outdir, exist_ok=True)
    y, m = start_ym
    ok = miss = fail = cached = 0
    while (y, m) <= end_ym:
        ms = f"{y:04d}-{m:02d}"
        fname = f"{symbol}-{category}-{ms}.zip"
        url = f"{BASE}/futures/{market}/monthly/{category}/{symbol}/{fname}"
        dest = os.path.join(outdir, fname)
        status = fetch(url, dest)
        if status == "ok":
            ok += 1
        elif status == "missing":
            miss += 1
        elif status == "cached":
            cached += 1
        else:
            fail += 1
        m += 1
        if m > 12:
            m = 1
            y += 1
    print(f"[{label}] DONE ok={ok} cached={cached} missing={miss} failed={fail}", flush=True)


if __name__ == "__main__":
    today = dt.date.today()

    print("=== BTCUSDT 5m klines (um) 2020-09-01 -> today ===", flush=True)
    download_daily("klines", "BTCUSDT", dt.date(2020, 9, 1), today, market="um",
                    subdir="5m", label="klines_5m")

    print("=== BTCUSDT metrics (OI, top-trader ratio, taker vol) 2020-09-01 -> today ===", flush=True)
    download_daily("metrics", "BTCUSDT", dt.date(2020, 9, 1), today, market="um", label="metrics")

    print("=== BTCUSDT funding rate monthly 2020-01 -> today ===", flush=True)
    download_monthly("fundingRate", "BTCUSDT", (2020, 1), (today.year, today.month),
                      market="um", label="funding")

    print("=== BTCUSD_PERP real liquidationSnapshot 2023-06-25 -> 2024-10-14 ===", flush=True)
    download_daily("liquidationSnapshot", "BTCUSD_PERP", dt.date(2023, 6, 25), dt.date(2024, 10, 14),
                    market="cm", label="liq_real_btcusd_perp")

    print("=== BTCUSD_PERP 5m klines (cm, for calibration window) 2023-06-20 -> 2024-10-20 ===", flush=True)
    download_daily("klines", "BTCUSD_PERP", dt.date(2023, 6, 20), dt.date(2024, 10, 20), market="cm",
                    subdir="5m", label="liq_real_klines")

    print("ALL DONE", flush=True)
