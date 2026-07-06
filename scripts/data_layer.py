"""Phase 0 — multi-asset data layer for the research lab (PLAN_BACKTESTS.md).

One module, three sources, all cached under state/data_cache/ :

  fetch_klines(symbol, interval, n)   Binance spot OHLCV, any symbol/interval.
                                      Same bar dict shape as baseline_ak_macd
                                      ({time,open,high,low,close,volume}, time in s).
                                      Gold intraday = PAXGUSDT (listed 2019-09).
  fetch_daily(ticker, start)          yfinance daily bars -> same dict shape.
                                      GC=F (gold, 2000->), DX-Y.NYB (dollar index —
                                      DX=F is dead on Yahoo), NQ=F, ZB=F, ^NDX ...
                                      for regime/correlation work.
  fetch_cot(years, market_substr)     CFTC legacy futures-only annual files.
                                      Returns one row per weekly report with net
                                      positions AND `usable_from` = release date
                                      (report Tuesday + 3 days = Friday) — ALWAYS
                                      join on usable_from, never on report date
                                      (3-day publication lag, see tv_catalogue #3).

Cache: JSON (klines) / CSV (yf, cot) files keyed by args; delete a file to force
a refetch. No cache TTL — research data is append-only history.

Usage (smoke test): uv run python scripts/data_layer.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO, "state", "data_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_UA = {"User-Agent": "0rum-research"}


# ---------------------------------------------------------------- Binance klines
def fetch_klines(symbol: str = "BTCUSDT", interval: str = "15m", n: int = 50000,
                 cache: bool = True) -> list[dict]:
    """Paginated Binance spot klines, newest-last. Cached per (symbol, interval, n-bucket)."""
    path = os.path.join(CACHE_DIR, f"klines_{symbol}_{interval}.json")
    if cache and os.path.exists(path):
        with open(path) as f:
            rows = json.load(f)
        if len(rows) >= n:
            print(f"  (cache hit {symbol} {interval}: {len(rows)} bars)", flush=True)
            return rows[-n:]
    out: list[dict] = []
    end = int(time.time() * 1000)
    while len(out) < n:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&limit=1000&endTime={end}")
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            batch = json.loads(r.read().decode())
        if not batch:
            break
        rows = [{"time": k[0] // 1000, "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in batch]
        out = rows + out
        end = batch[0][0] - 1
        if len(out) % 50000 < 1000:
            print(f"  {symbol} {interval}: {len(out)} bars ...", flush=True)
        time.sleep(0.25)
        if len(batch) < 1000:
            break
    out = out[-n:]
    if cache:
        with open(path, "w") as f:
            json.dump(out, f)
    print(f"  (fetched + cached {symbol} {interval}: {len(out)} bars)", flush=True)
    return out


# ---------------------------------------------------------------- yfinance daily
def fetch_daily(ticker: str, start: str = "2000-01-01", cache: bool = True) -> list[dict]:
    """Daily bars via yfinance, same dict shape (time = UTC midnight seconds)."""
    safe = ticker.replace("=", "_").replace("^", "_").replace("-", "_").replace(".", "_")
    path = os.path.join(CACHE_DIR, f"daily_{safe}.csv")
    if cache and os.path.exists(path):
        with open(path) as f:
            rows = [{"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                     "low": float(r["low"]), "close": float(r["close"]),
                     "volume": float(r["volume"])} for r in csv.DictReader(f)]
        print(f"  (cache hit {ticker}: {len(rows)} days)", flush=True)
        return rows
    import yfinance as yf  # heavy import kept local
    df = yf.download(ticker, start=start, progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned nothing for {ticker}")
    if hasattr(df.columns, "levels"):  # MultiIndex when single ticker (yf >= 0.2.40)
        df.columns = [c[0] for c in df.columns]
    rows = []
    for idx, r in df.iterrows():
        ts = int(datetime(idx.year, idx.month, idx.day, tzinfo=timezone.utc).timestamp())
        rows.append({"time": ts, "open": float(r["Open"]), "high": float(r["High"]),
                     "low": float(r["Low"]), "close": float(r["Close"]),
                     "volume": float(r.get("Volume", 0) or 0)})
    if cache:
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["time", "open", "high", "low", "close", "volume"])
            w.writeheader()
            w.writerows(rows)
    print(f"  (fetched + cached {ticker}: {len(rows)} days)", flush=True)
    return rows


# ---------------------------------------------------------------- CFTC COT (legacy futures-only)
COT_URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"

# Legacy annual.txt column names (CFTC "futures only" report)
_COT_COLS = {
    "market": "Market and Exchange Names",
    "date": "As of Date in Form YYYY-MM-DD",
    "comm_long": "Commercial Positions-Long (All)",
    "comm_short": "Commercial Positions-Short (All)",
    "noncomm_long": "Noncommercial Positions-Long (All)",
    "noncomm_short": "Noncommercial Positions-Short (All)",
    "nonrep_long": "Nonreportable Positions-Long (All)",
    "nonrep_short": "Nonreportable Positions-Short (All)",
    "oi": "Open Interest (All)",
}


def fetch_cot(years: range, market_substr: str, cache: bool = True) -> list[dict]:
    """Weekly legacy COT rows for markets whose name STARTS WITH `market_substr`
    (case-insensitive). Prefix match, not contains: "GOLD - COMMODITY EXCHANGE"
    must NOT also catch "MICRO GOLD - COMMODITY EXCHANGE INC.". E.g. "BITCOIN".

    Each row: report_date, usable_from (report Tue + 3d = release Fri —
    JOIN PRICES ON THIS), comm_net, noncomm_net, nonrep_net, oi.
    """
    key = market_substr.lower().replace(" ", "_").replace("-", "")[:24]
    path = os.path.join(CACHE_DIR, f"cot_{key}_{years.start}_{years.stop - 1}.csv")
    if cache and os.path.exists(path):
        with open(path) as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            for k in ("comm_net", "noncomm_net", "nonrep_net", "oi"):
                r[k] = float(r[k])
        print(f"  (cache hit COT '{market_substr}': {len(rows)} weeks)", flush=True)
        return rows

    import httpx  # urllib fails on cftc.gov cert chain with macOS system Python

    out: list[dict] = []
    for year in years:
        url = COT_URL.format(year=year)
        try:
            resp = httpx.get(url, headers=_UA, timeout=60, follow_redirects=True)
            resp.raise_for_status()
            blob = resp.content
        except Exception as e:  # year not published yet
            print(f"  COT {year}: skip ({e})", flush=True)
            continue
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
            text = z.read(name).decode("latin-1")
        rdr = csv.DictReader(io.StringIO(text))
        for row in rdr:
            mkt = (row.get(_COT_COLS["market"]) or "").strip()
            if not mkt.lower().startswith(market_substr.lower()):
                continue
            d = (row.get(_COT_COLS["date"]) or "").strip()
            try:
                report = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            g = lambda k: float((row.get(_COT_COLS[k]) or "0").replace(",", "") or 0)
            out.append({
                "market": mkt,
                "report_date": report.strftime("%Y-%m-%d"),
                "usable_from": (report + timedelta(days=3)).strftime("%Y-%m-%d"),
                "comm_net": g("comm_long") - g("comm_short"),
                "noncomm_net": g("noncomm_long") - g("noncomm_short"),
                "nonrep_net": g("nonrep_long") - g("nonrep_short"),
                "oi": g("oi"),
            })
        print(f"  COT {year}: total {len(out)} rows for '{market_substr}'", flush=True)
        time.sleep(0.5)
    out.sort(key=lambda r: r["report_date"])
    if cache and out:
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
    return out


def cot_index(values: list[float], lookback: int = 156) -> list[float]:
    """Briese-style COT index: percentile rank 0-100 of net position over
    `lookback` weeks (156 = 3 years). First `lookback` values use what exists."""
    out = []
    for i, v in enumerate(values):
        win = values[max(0, i - lookback + 1): i + 1]
        lo, hi = min(win), max(win)
        out.append(50.0 if hi == lo else 100.0 * (v - lo) / (hi - lo))
    return out


# ---------------------------------------------------------------- smoke test
if __name__ == "__main__":
    print("== Binance PAXG (gold) 4h ==")
    paxg = fetch_klines("PAXGUSDT", "4h", 1500)
    f = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"   {len(paxg)} bars  {f(paxg[0]['time'])} -> {f(paxg[-1]['time'])}  close {paxg[-1]['close']:.0f}")

    print("== Binance ETH 4h ==")
    eth = fetch_klines("ETHUSDT", "4h", 1500)
    print(f"   {len(eth)} bars  {f(eth[0]['time'])} -> {f(eth[-1]['time'])}  close {eth[-1]['close']:.0f}")

    print("== yfinance GC=F (gold futures daily) ==")
    gc = fetch_daily("GC=F", "2005-01-01")
    print(f"   {len(gc)} days  {f(gc[0]['time'])} -> {f(gc[-1]['time'])}  close {gc[-1]['close']:.0f}")

    print("== yfinance DX-Y.NYB (dollar index daily) ==")
    dxy = fetch_daily("DX-Y.NYB", "2005-01-01")
    print(f"   {len(dxy)} days  {f(dxy[0]['time'])} -> {f(dxy[-1]['time'])}  close {dxy[-1]['close']:.2f}")

    print("== CFTC COT gold (COMEX) 2023-2026 ==")
    cot = fetch_cot(range(2023, 2027), "GOLD - COMMODITY EXCHANGE")
    if cot:
        idx = cot_index([r["comm_net"] for r in cot])
        last = cot[-1]
        print(f"   {len(cot)} weeks  last report {last['report_date']} (usable {last['usable_from']})"
              f"  comm_net {last['comm_net']:+.0f}  COT-index {idx[-1]:.0f}")

    print("\nPhase 0 data layer OK.")
