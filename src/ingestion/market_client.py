"""Binance market data client — PAXG/USDT as XAU/USD proxy, no authentication required.

NOTE: PAXG/USDT is a gold-backed ERC-20 token traded on Binance against USDT.
It is used as a proxy for XAU/USD during prototyping and backtesting because
Binance's public API requires zero authentication. Differences vs real XAU/USD:
  - Crypto microstructure (order book, liquidity profile)
  - 24/7 trading (Forex closes on weekends)
  - USDT denomination (not USD)
Before Phase 7 (execution): validate strategy signals against a real XAU/USD
source and connect a live Forex broker for order placement.
"""

import ccxt.async_support as ccxt
import structlog
from datetime import datetime, timezone

from src.config import Settings

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


class MarketDataClient:
    """Async Binance client for PAXG/USDT candle data — no API key required."""

    def __init__(self, settings: Settings | None = None) -> None:
        # No credentials needed — Binance klines endpoint is public
        self._exchange = ccxt.binance({"enableRateLimit": True})

    async def get_candles(
        self,
        instrument: str,
        granularity: str,
        count: int = BINANCE_MAX_CANDLES,
        from_time: str | None = None,
    ) -> list[dict]:
        """Fetch up to 1000 candles from Binance for PAXG/USDT.

        Args:
            instrument: e.g. "XAUUSD" (mapped to PAXG/USDT internally)
            granularity: "M15", "H1", "H4", or "D1"
            count: Max candles per call (Binance cap: 1000)
            from_time: ISO 8601 UTC string — if set, fetches forward from this time

        Returns:
            List of normalized candle dicts:
            {time (ISO str), open, high, low, close, volume}
        """
        symbol = _INSTRUMENT_MAP.get(instrument, instrument)
        tf = _TF_MAP.get(granularity, granularity)
        since_ms: int | None = None
        if from_time:
            dt = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
            since_ms = int(dt.timestamp() * 1000)

        try:
            raw = await self._exchange.fetch_ohlcv(
                symbol,
                timeframe=tf,
                since=since_ms,
                limit=min(count, BINANCE_MAX_CANDLES),
            )
            candles = [self._normalize(c) for c in (raw or [])]
            log.info(
                "market_client.fetched",
                instrument=instrument,
                symbol=symbol,
                granularity=granularity,
                count=len(candles),
                from_time=from_time,
            )
            return candles

        except ccxt.NetworkError as e:
            log.error("market_client.network_error", error=str(e))
            raise
        except ccxt.ExchangeError as e:
            log.error("market_client.exchange_error", error=type(e).__name__)
            raise
        except Exception as e:
            log.error("market_client.fetch_error", error=type(e).__name__)
            raise

    @staticmethod
    def _normalize(ohlcv: list) -> dict:
        """Normalize a ccxt OHLCV row [ts_ms, open, high, low, close, volume] to a dict."""
        ts_ms, open_, high, low, close, volume = ohlcv
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return {
            "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
        }
