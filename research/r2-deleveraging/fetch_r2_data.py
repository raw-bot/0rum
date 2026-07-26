"""R2 data harvest. Dev: Binance 2020-09..2023-12. Confirmation: Kraken 2024-01..2026-07 (no analysis before freeze).
Resumable: skips existing files."""
import datetime, json, pathlib, time, urllib.request, sys

HERE = pathlib.Path(__file__).parent
D = HERE / "data"
UA = {"User-Agent": "Mozilla/5.0"}
def get(url, tries=3, timeout=60):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
        except Exception as e:
            if i == tries - 1:
                print(f"FAIL {url}: {e}", flush=True); return None
            time.sleep(2)

def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += datetime.timedelta(days=1)

def monthrange(a, b):
    y, m = a
    while (y, m) <= b:
        yield y, m
        m += 1
        if m == 13: y, m = y + 1, 1

SYMS = ["BTCUSDT", "ETHUSDT"]
# 1) Binance daily metrics (dev window)
n_ok = n_miss = 0
for sym in SYMS:
    out = D / "binance_metrics" / sym; out.mkdir(parents=True, exist_ok=True)
    for d in daterange(datetime.date(2020, 9, 1), datetime.date(2023, 12, 31)):
        f = out / f"{sym}-metrics-{d}.zip"
        if f.exists(): continue
        raw = get(f"https://data.binance.vision/data/futures/um/daily/metrics/{sym}/{sym}-metrics-{d}.zip", tries=2, timeout=30)
        if raw and raw[:2] == b"PK": f.write_bytes(raw); n_ok += 1
        else: n_miss += 1
        time.sleep(0.05)
print(f"metrics done ok={n_ok} miss={n_miss}", flush=True)

# 2) Binance monthly 1m klines (dev window)
for sym in SYMS:
    out = D / "binance_klines" / sym; out.mkdir(parents=True, exist_ok=True)
    for y, m in monthrange((2020, 9), (2023, 12)):
        f = out / f"{sym}-1m-{y}-{m:02d}.zip"
        if f.exists(): continue
        raw = get(f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/1m/{sym}-1m-{y}-{m:02d}.zip", timeout=180)
        if raw and raw[:2] == b"PK": f.write_bytes(raw); print(f"klines {sym} {y}-{m:02d} {len(raw)//1024}KB", flush=True)
        time.sleep(0.1)
print("klines done", flush=True)

# 3) Binance monthly funding (full span: regime variable)
for sym in SYMS:
    out = D / "binance_funding" / sym; out.mkdir(parents=True, exist_ok=True)
    for y, m in monthrange((2020, 9), (2026, 7)):
        f = out / f"{sym}-fundingRate-{y}-{m:02d}.zip"
        if f.exists(): continue
        raw = get(f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{y}-{m:02d}.zip", tries=2, timeout=30)
        if raw and raw[:2] == b"PK": f.write_bytes(raw)
        time.sleep(0.05)
print("funding done", flush=True)

# 4) Kraken confirmation harvest (2024-01-01 .. 2026-07-24), 2-day chunks — STORED, NOT ANALYZED
KSYMS = ["PF_XBTUSD", "PF_ETHUSD"]
start, end = datetime.date(2024, 1, 1), datetime.date(2026, 7, 24)
for sym in KSYMS:
    out = D / "kraken" / sym; out.mkdir(parents=True, exist_ok=True)
    d = start
    while d <= end:
        d2 = min(d + datetime.timedelta(days=2), end + datetime.timedelta(days=1))
        t0 = int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.UTC).timestamp())
        t1 = int(datetime.datetime(d2.year, d2.month, d2.day, tzinfo=datetime.UTC).timestamp())
        foi = out / f"oi-{d}.json"
        fpx = out / f"px-{d}.json"
        if not foi.exists():
            raw = get(f"https://futures.kraken.com/api/charts/v1/analytics/{sym}/open-interest?since={t0}&to={t1}&interval=300", timeout=45)
            if raw: foi.write_bytes(raw)
            time.sleep(0.12)
        if not fpx.exists():
            raw = get(f"https://futures.kraken.com/api/charts/v1/trade/{sym}/1m?from={t0}&to={t1}", timeout=60)
            if raw: fpx.write_bytes(raw)
            time.sleep(0.12)
        d = d2
    print(f"kraken {sym} done", flush=True)
print("ALL DONE", flush=True)
