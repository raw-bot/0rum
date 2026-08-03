import os
import threading
import time

import httpx

from orum.dsl import CANDLE_BUFFER

_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Cache for the dedicated timeframe window (see recent_closed_candles).
_tf_lock = threading.Lock()
_tf_cache: dict = {"ts": 0.0, "candles": [], "asset": None, "interval": None}
_TF_TTL_SECONDS = 60.0


def _binance_symbol(asset: str) -> str:
    return asset.replace("/", "").upper()


def _offline_payload(asset: str) -> dict:
    base = 65000.0
    closes = [base - (index * 12.5) for index in range(CANDLE_BUFFER)]
    candles = [
        {
            "ts": f"offline-{index}",
            "open": close + 12.5,
            "high": close + 20.0,
            "low": close - 7.5,
            "close": close,
            "volume": 1.0,
        }
        for index, close in enumerate(closes)
    ]
    return {
        "schema_version": 1,
        "source": "offline_fallback",
        "asset": asset,
        "last": closes[-1],
        "last_candle_ts": f"offline-{CANDLE_BUFFER - 1}",
        "closes": closes,
        "candles": candles,
    }


def _candles_from_klines(rows: list) -> list[dict]:
    return [
        {
            "ts": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


def recent_closed_candles(asset: str = "BTC/USDT", interval: str = "15m", *, limit: int = 120) -> list[dict]:
    """Cached Binance CLOSED klines for indicators that need a specific timeframe.

    The worker's own market feed is 1m and only CANDLE_BUFFER bars deep
    — far too short for indicators operating on larger timeframes. The SSL trail
    needs the SAME baseline the chart and the signal brain use, so it pulls a
    dedicated window here. The in-progress (forming) bar is DROPPED — like the
    producer — so the band and the "red line" only ever reflect CLOSED bars, never
    a mid-bar 1m flicker. Cached for _TF_TTL_SECONDS (the loop polls every ~60s).
    Returns [] on any failure, so the caller SKIPS the trail rather than computing
    the band on the wrong (1m) timeframe."""
    now = time.time()
    with _tf_lock:
        cache = _tf_cache
        if cache["candles"] and cache["asset"] == asset and cache["interval"] == interval and (now - cache["ts"]) < _TF_TTL_SECONDS:
            return cache["candles"]
    params = {"symbol": _binance_symbol(asset), "interval": interval, "limit": limit}
    try:
        with httpx.Client(timeout=8) as client:
            response = client.get(_KLINES_URL, params=params)
            response.raise_for_status()
        candles = _candles_from_klines(response.json())[:-1]  # drop the in-progress bar
    except (httpx.HTTPError, OSError, ValueError):
        return []
    if candles:
        with _tf_lock:
            _tf_cache.update(ts=now, candles=candles, asset=asset, interval=interval)
    return candles


async def fetch(asset: str | None = None) -> dict:
    asset = asset or os.getenv("0RUM_ASSET", "BTC/USDT")
    symbol = _binance_symbol(asset)
    url = "https://api.binance.com/api/v3/klines"
    # CANDLE_BUFFER candles cover the deepest DSL warm-up the schema accepts;
    # one klines call per tick is still enough.
    params = {"symbol": symbol, "interval": "1m", "limit": CANDLE_BUFFER}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        rows = response.json()
        candles = _candles_from_klines(rows)
        closes = [candle["close"] for candle in candles]
        return {
            "schema_version": 1,
            "source": "binance_public",
            "asset": asset,
            "last": closes[-1],
            "last_candle_ts": candles[-1]["ts"],
            "closes": closes,
            "candles": candles,
        }
    except (httpx.HTTPError, OSError, ValueError):
        return _offline_payload(asset)
