"""MetaAPI async market data client — connects Python to FXCM MT4 via MetaAPI cloud."""

from datetime import datetime, timezone
from typing import Any

import structlog

from src.config import Settings

log = structlog.get_logger(__name__)

# Internal timeframe → MetaAPI period string
_TF_MAP: dict[str, str] = {
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}


class MarketDataClient:
    """Async MetaAPI client for historical candle fetching from FXCM MT4."""

    def __init__(self, settings: Settings) -> None:
        # Credentials stored only as private attributes — never logged
        self._token = settings.metaapi_token
        self._account_id = settings.metaapi_account_id
        self._api: Any = None
        self._account: Any = None

    async def _ensure_connected(self) -> Any:
        """Lazy-initialize MetaApi and ensure account is deployed + connected."""
        from metaapi_cloud_sdk import MetaApi  # import deferred — avoids import-time side effects

        if self._api is None:
            self._api = MetaApi(self._token)

        if self._account is None:
            self._account = await self._api.metatrader_account_api.get_account(self._account_id)

        if self._account.state not in ("DEPLOYED", "DEPLOYING"):
            await self._account.deploy()

        await self._account.wait_connected()
        return self._account

    async def get_candles(
        self,
        instrument: str,
        granularity: str,
        count: int = 500,
        from_time: str | None = None,
    ) -> list[dict]:
        """Fetch historical candles from MetaAPI (FXCM MT4).

        Args:
            instrument: e.g. "XAUUSD"
            granularity: "M15", "H1", "H4", or "D1"
            count: Max candles to return (ignored when from_time is set)
            from_time: ISO 8601 UTC string — if set, fetches from this time onward

        Returns:
            List of normalized candle dicts with keys:
            time (ISO str), open, high, low, close, tickVolume
        """
        tf = _TF_MAP.get(granularity, granularity)
        start_dt: datetime | None = None
        if from_time:
            start_dt = datetime.fromisoformat(from_time.replace("Z", "+00:00"))

        try:
            account = await self._ensure_connected()
            raw = await account.get_historical_candles(
                symbol=instrument,
                timeframe=tf,
                start_time=start_dt,
                count=count if not from_time else None,
            )
            candles = [self._normalize(c) for c in (raw or [])]
            log.info(
                "market_client.fetched",
                instrument=instrument,
                granularity=granularity,
                count=len(candles),
                from_time=from_time,
            )
            return candles

        except Exception as e:
            log.error(
                "market_client.fetch_error",
                instrument=instrument,
                granularity=granularity,
                error=type(e).__name__,
                # NEVER log token, account_id, or response body
            )
            raise

    @staticmethod
    def _normalize(raw: dict) -> dict:
        """Normalize a MetaAPI candle dict to a consistent internal format."""
        ts = raw.get("time")
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            ts_str = str(ts) if ts is not None else None

        return {
            "time": ts_str,
            "open": float(raw.get("open", 0)),
            "high": float(raw.get("high", 0)),
            "low": float(raw.get("low", 0)),
            "close": float(raw.get("close", 0)),
            "tickVolume": int(raw.get("tickVolume", raw.get("volume", 0))),
        }
