import os


async def fetch() -> dict:
    api_key = os.getenv("NEWS_API_KEY")
    if api_key:
        return {
            "schema_version": 1,
            "source": "news_api_configured",
            "sentiment": "neutral",
        }

    # The previously used CoinDesk BPI endpoint is discontinued; every call
    # ended in the offline fallback. Without an API key there is no live news
    # source, so say so explicitly instead of issuing a doomed HTTP request.
    return {
        "schema_version": 1,
        "source": "not_configured",
        "headline_count": 0,
        "sentiment": "unknown",
        "context": "no news provider configured",
    }
