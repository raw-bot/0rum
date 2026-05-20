"""Unit tests for MarketDataClient — ccxt exchange is mocked."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.config import Settings
from src.ingestion.market_client import MarketDataClient


def _raw_candle(ts_ms: int = 1704153600000) -> list:
    """One ccxt OHLCV row: [ts_ms, open, high, low, close, volume]."""
    return [ts_ms, 2000.0, 2010.0, 1995.0, 2005.0, 150.0]


def _make_client(fetch_return: list) -> MarketDataClient:
    """Build a MarketDataClient with a mocked ccxt exchange."""
    client = MarketDataClient.__new__(MarketDataClient)
    client._provider = "binance"
    client._exchange = AsyncMock()
    client._exchange.fetch_ohlcv = AsyncMock(return_value=fetch_return)
    return client


@pytest.mark.asyncio
async def test_get_candles_returns_normalized_dicts():
    """get_candles returns flat dicts with open/high/low/close/volume."""
    client = _make_client([_raw_candle()])
    candles = await client.get_candles("XAUUSD", "M15", count=1)

    assert len(candles) == 1
    c = candles[0]
    assert c["open"] == 2000.0
    assert c["high"] == 2010.0
    assert c["low"] == 1995.0
    assert c["close"] == 2005.0
    assert c["volume"] == 150.0
    assert "time" in c


@pytest.mark.asyncio
async def test_xauusd_mapped_to_paxg_usdt():
    """XAUUSD instrument is sent to Binance as PAXG/USDT."""
    client = _make_client([])
    await client.get_candles("XAUUSD", "M15", count=5)

    call_args = client._exchange.fetch_ohlcv.call_args
    assert call_args.args[0] == "PAXG/USDT"


@pytest.mark.asyncio
async def test_m15_mapped_to_15m():
    """M15 timeframe is sent as '15m' to ccxt."""
    client = _make_client([])
    await client.get_candles("XAUUSD", "M15", count=5)

    call_kwargs = client._exchange.fetch_ohlcv.call_args.kwargs
    assert call_kwargs["timeframe"] == "15m"


@pytest.mark.asyncio
async def test_d1_mapped_to_1d():
    """D1 timeframe is sent as '1d' to ccxt."""
    client = _make_client([])
    await client.get_candles("XAUUSD", "D1", count=5)

    call_kwargs = client._exchange.fetch_ohlcv.call_args.kwargs
    assert call_kwargs["timeframe"] == "1d"


@pytest.mark.asyncio
async def test_from_time_converted_to_since_ms():
    """from_time ISO string is converted to millisecond timestamp for ccxt."""
    client = _make_client([])
    await client.get_candles("XAUUSD", "H1", from_time="2024-01-02T00:00:00Z")

    call_kwargs = client._exchange.fetch_ohlcv.call_args.kwargs
    # 2024-01-02T00:00:00Z = 1704153600000 ms
    assert call_kwargs["since"] == 1704153600000


@pytest.mark.asyncio
async def test_timestamp_normalized_to_utc_iso_string():
    """Candle time is returned as ISO UTC string, not raw milliseconds."""
    client = _make_client([_raw_candle(ts_ms=1704153600000)])
    candles = await client.get_candles("XAUUSD", "M15", count=1)

    assert candles[0]["time"] == "2024-01-02T00:00:00Z"


@pytest.mark.asyncio
async def test_empty_response_returns_empty_list():
    """If Binance returns no candles, get_candles returns []."""
    client = _make_client([])
    candles = await client.get_candles("XAUUSD", "H4", count=5)

    assert candles == []


@pytest.mark.asyncio
async def test_ig_provider_routes_to_ig_client():
    """When configured for IG, MarketDataClient delegates directly to IGClient."""
    settings = Settings(
        database_url="sqlite+aiosqlite:///./test.db",
        redis_url="redis://localhost:6379/0",
        market_data_provider="ig",
    )
    client = MarketDataClient.__new__(MarketDataClient)
    client.settings = settings
    client._provider = "ig"
    client._exchange = None
    client._ig_client = AsyncMock()
    client._ig_client.get_candles = AsyncMock(
        return_value=[{"time": "2026-04-13T12:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    )

    candles = await client.get_candles(
        "XAUUSD",
        "M15",
        count=3,
        from_time="2026-04-13T00:00:00Z",
        to_time="2026-04-13T12:00:00Z",
    )

    assert len(candles) == 1
    client._ig_client.get_candles.assert_awaited_once_with(
        instrument="XAUUSD",
        granularity="M15",
        count=3,
        from_time="2026-04-13T00:00:00Z",
        to_time="2026-04-13T12:00:00Z",
    )
