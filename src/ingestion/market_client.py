"""Market data client for the runtime Binance/PAXG plumbing feed."""

from datetime import datetime, timezone

import ccxt.async_support as ccxt
import structlog

from src.config import MarketDataProvider, Settings, get_settings

log = structlog.get_logger(__name__)

# Internal timeframe → ccxt/Binance timeframe string
_TF_MAP: dict[str, str] = {
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}

# Internal instrument → Binance symbol
_INSTRUMENT_MAP: dict[str, str] = {
    "XAUUSD": "PAXG/USDT",
}

# Binance hard limit per request
BINANCE_MAX_CANDLES = 1000
RUNTIME_PROXY_SOURCE_KIND = "runtime_proxy"


class MarketDataClient:
    """Async market-data client for the configured runtime provider."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._provider = (
            self.settings.market_data_provider.value
            if isinstance(self.settings.market_data_provider, MarketDataProvider)
            else str(self.settings.market_data_provider).lower()
        )
        self._exchange = None

        if self._provider == MarketDataProvider.BINANCE.value:
            # No credentials needed — Binance klines endpoint is public.
            self._exchange = ccxt.binance({"enableRateLimit": True})
        else:
            raise RuntimeError(
                f"Unsupported market data provider '{self._provider}'. "
                "Use 'binance'."
            )

    async def aclose(self) -> None:
        """Close any provider-specific network clients."""
        if self._exchange is not None:
            await self._exchange.close()

    async def get_candles(
        self,
        instrument: str,
        granularity: str,
        count: int = BINANCE_MAX_CANDLES,
        from_time: str | None = None,
        to_time: str | None = None,
    ) -> list[dict]:
        """Fetch normalized candles from the selected market-data provider.

        Args:
            instrument: e.g. "XAUUSD"
            granularity: "M15", "H1", "H4", or "D1"
            count: Max candles per call
            from_time: ISO 8601 UTC string — if set, fetches forward from this time
            to_time: Optional ISO 8601 UTC string kept for caller compatibility

        Returns:
            List of normalized candle dicts:
            {time (ISO str), open, high, low, close, volume}
        """
        provider = getattr(self, "_provider", MarketDataProvider.BINANCE.value)

        symbol = _INSTRUMENT_MAP.get(instrument, instrument)
        tf = _TF_MAP.get(granularity, granularity)
        since_ms: int | None = None
        if from_time:
            dt = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
            since_ms = int(dt.timestamp() * 1000)

        try:
            assert self._exchange is not None
            raw = await self._exchange.fetch_ohlcv(
                symbol,
                timeframe=tf,
                since=since_ms,
                limit=min(count, BINANCE_MAX_CANDLES),
            )
            source_metadata = {
                "source_kind": RUNTIME_PROXY_SOURCE_KIND,
                "proxy_symbol": symbol,
                "canonical_instrument": instrument,
            }
            candles = [
                self._normalize(c, source_metadata=source_metadata)
                for c in (raw or [])
            ]
            log.info(
                "market_client.fetched",
                provider=provider,
                source_kind=RUNTIME_PROXY_SOURCE_KIND,
                instrument=instrument,
                proxy_symbol=symbol,
                canonical_instrument=instrument,
                granularity=granularity,
                count=len(candles),
                from_time=from_time,
                to_time=to_time,
            )
            return candles

        except ccxt.NetworkError as e:
            log.error("market_client.network_error", provider=provider, error=str(e))
            raise
        except ccxt.ExchangeError as e:
            log.error(
                "market_client.exchange_error",
                provider=provider,
                error=type(e).__name__,
            )
            raise
        except Exception as e:
            log.error("market_client.fetch_error", provider=provider, error=type(e).__name__)
            raise

    @staticmethod
    def _normalize(ohlcv: list, *, source_metadata: dict | None = None) -> dict:
        """Normalize a ccxt OHLCV row [ts_ms, open, high, low, close, volume] to a dict."""
        ts_ms, open_, high, low, close, volume = ohlcv
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        normalized = {
            "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
        }
        if source_metadata:
            normalized.update(source_metadata)
        return normalized
