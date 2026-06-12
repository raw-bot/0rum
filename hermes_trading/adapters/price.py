import os

import httpx

from hermes_trading.dsl import CANDLE_BUFFER


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


async def fetch(asset: str | None = None) -> dict:
    asset = asset or os.getenv("HERMES_ASSET", "BTC/USDT")
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
