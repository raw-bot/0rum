import os

import httpx


async def fetch() -> dict:
    api_key = os.getenv("NEWS_API_KEY")
    if api_key:
        return {
            "schema_version": 1,
            "source": "news_api_configured",
            "sentiment": "neutral",
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get("https://api.coindesk.com/v1/bpi/currentprice.json")
            response.raise_for_status()
        data = response.json()
        return {
            "schema_version": 1,
            "source": "coindesk_public",
            "headline_count": 1,
            "sentiment": "neutral",
            "context": data.get("chartName", "Bitcoin"),
        }
    except (httpx.HTTPError, OSError, ValueError):
        return {
            "schema_version": 1,
            "source": "offline_fallback",
            "headline_count": 0,
            "sentiment": "unknown",
            "context": "offline",
        }
