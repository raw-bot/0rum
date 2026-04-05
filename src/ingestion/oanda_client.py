"""OANDA v20 async candle client."""

import structlog
import httpx

from src.config import Settings

log = structlog.get_logger(__name__)


class OandaClient:
    """Async client for OANDA v20 API — candle fetching only."""

    BASE_HEADERS = {"Content-Type": "application/json"}

    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.oanda_api_url
        self.account_id = settings.oanda_account_id
        # NOTE: API key stored only in headers dict — NEVER logged
        self.headers = {
            **self.BASE_HEADERS,
            "Authorization": f"Bearer {settings.oanda_api_key}",
        }

    async def get_candles(
        self,
        instrument: str,
        granularity: str,
        count: int = 500,
        from_time: str | None = None,
    ) -> list[dict]:
        """Fetch candles from OANDA v20.

        Args:
            instrument: e.g. "XAUUSD"
            granularity: "M15", "H1", "H4", or "D1" (D1 is mapped to "D" for OANDA)
            count: Number of candles to fetch (ignored when from_time is set)
            from_time: ISO 8601 UTC string — if set, fetches from this time onward

        Returns:
            List of raw candle dicts from OANDA response["candles"]
        """
        oanda_gran = "D" if granularity == "D1" else granularity
        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params: dict = {
            "granularity": oanda_gran,
            "count": count,
            "price": "MBA",
        }
        if from_time:
            params["from"] = from_time
            del params["count"]  # OANDA: count and from are mutually exclusive

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=self.headers, params=params)
                resp.raise_for_status()
                data = resp.json()
                candles = data.get("candles", [])
                log.info(
                    "oanda_client.fetched",
                    instrument=instrument,
                    granularity=granularity,
                    count=len(candles),
                    from_time=from_time,
                )
                return candles
        except httpx.HTTPStatusError as e:
            log.error(
                "oanda_client.http_error",
                status_code=e.response.status_code,
                url=str(e.response.url),
                # NEVER log response body or request headers (auth exposure risk)
            )
            raise
        except httpx.RequestError as e:
            log.error("oanda_client.request_error", error=type(e).__name__)
            raise
