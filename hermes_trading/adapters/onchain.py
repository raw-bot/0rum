import os

import httpx


async def fetch() -> dict:
    api_key = os.getenv("GLASSNODE_API_KEY")
    if api_key:
        return {
            "schema_version": 1,
            "source": "glassnode_configured",
            "signals": {"premium_enabled": True},
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get("https://api.blockchain.info/stats")
            response.raise_for_status()
        data = response.json()
        return {
            "schema_version": 1,
            "source": "blockchain_info_public",
            "signals": {
                "market_price_usd": data.get("market_price_usd"),
                "hash_rate": data.get("hash_rate"),
                "n_tx": data.get("n_tx"),
            },
        }
    except (httpx.HTTPError, OSError, ValueError):
        return {
            "schema_version": 1,
            "source": "offline_fallback",
            "signals": {
                "market_price_usd": None,
                "hash_rate": None,
                "n_tx": None,
            },
        }
