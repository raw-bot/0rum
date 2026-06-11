import csv
from io import StringIO

import httpx


async def fetch() -> dict:
    url = "https://stooq.com/q/l/"
    params = {"s": "dx.f", "f": "sd2t2ohlcv", "h": "", "e": "csv"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        rows = list(csv.DictReader(StringIO(response.text)))
        latest = rows[0] if rows else {}
        return {
            "schema_version": 1,
            "source": "stooq_public",
            "dxy_close": float(latest["Close"]) if latest.get("Close") not in (None, "N/D") else None,
        }
    except (httpx.HTTPError, OSError, ValueError):
        return {
            "schema_version": 1,
            "source": "offline_fallback",
            "dxy_close": None,
        }
