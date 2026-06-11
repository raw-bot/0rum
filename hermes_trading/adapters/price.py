import os

import httpx


def _binance_symbol(asset: str) -> str:
    return asset.replace("/", "").upper()


def _offline_payload(asset: str) -> dict:
    base = 65000.0
    closes = [base - (index * 12.5) for index in range(60)]
    return {
        "schema_version": 1,
        "source": "offline_fallback",
        "asset": asset,
        "last": closes[-1],
        "last_candle_ts": "offline-59",
        "closes": closes,
    }


async def fetch(asset: str | None = None) -> dict:
    asset = asset or os.getenv("HERMES_ASSET", "BTC/USDT")
    symbol = _binance_symbol(asset)
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": "1m", "limit": 60}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        rows = response.json()
        closes = [float(row[4]) for row in rows]
        return {
            "schema_version": 1,
            "source": "binance_public",
            "asset": asset,
            "last": closes[-1],
            "last_candle_ts": rows[-1][0],
            "closes": closes,
        }
    except (httpx.HTTPError, OSError, ValueError):
        return _offline_payload(asset)
