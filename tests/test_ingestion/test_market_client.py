"""Unit tests for MarketDataClient — MetaApi SDK is mocked."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingestion.market_client import MarketDataClient
from src.config import Settings


def make_settings() -> Settings:
    return Settings(
        metaapi_token="test-token",
        metaapi_account_id="test-account-id",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        telegram_bot_token="test-token",
        telegram_chat_id="test-chat",
    )


def make_raw_candle(ts: str = "2024-01-02T00:00:00Z") -> dict:
    return {
        "time": datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
        "open": 2000.0,
        "high": 2010.0,
        "low": 1995.0,
        "close": 2005.0,
        "tickVolume": 150,
    }


def _make_mock_account(candles: list) -> MagicMock:
    account = AsyncMock()
    account.state = "DEPLOYED"
    account.wait_connected = AsyncMock()
    account.get_historical_candles = AsyncMock(return_value=candles)
    return account


@pytest.mark.asyncio
@patch("src.ingestion.market_client.MarketDataClient._ensure_connected")
async def test_get_candles_returns_normalized_list(mock_connect):
    """get_candles returns normalized candle dicts with flat open/high/low/close."""
    mock_account = _make_mock_account([make_raw_candle()])
    mock_connect.return_value = mock_account

    client = MarketDataClient(make_settings())
    candles = await client.get_candles("XAUUSD", "M15", count=1)

    assert len(candles) == 1
    assert candles[0]["open"] == 2000.0
    assert candles[0]["high"] == 2010.0
    assert "time" in candles[0]


@pytest.mark.asyncio
@patch("src.ingestion.market_client.MarketDataClient._ensure_connected")
async def test_m15_mapped_to_15m(mock_connect):
    """M15 timeframe is sent as '15m' to MetaAPI."""
    mock_account = _make_mock_account([])
    mock_connect.return_value = mock_account

    client = MarketDataClient(make_settings())
    await client.get_candles("XAUUSD", "M15", count=5)

    call_kwargs = mock_account.get_historical_candles.call_args
    assert call_kwargs.kwargs.get("timeframe") == "15m"


@pytest.mark.asyncio
@patch("src.ingestion.market_client.MarketDataClient._ensure_connected")
async def test_d1_mapped_to_1d(mock_connect):
    """D1 timeframe is sent as '1d' to MetaAPI."""
    mock_account = _make_mock_account([])
    mock_connect.return_value = mock_account

    client = MarketDataClient(make_settings())
    await client.get_candles("XAUUSD", "D1", count=5)

    call_kwargs = mock_account.get_historical_candles.call_args
    assert call_kwargs.kwargs.get("timeframe") == "1d"


@pytest.mark.asyncio
@patch("src.ingestion.market_client.MarketDataClient._ensure_connected")
async def test_from_time_parsed_as_datetime(mock_connect):
    """When from_time is provided, start_time is passed as a datetime object."""
    mock_account = _make_mock_account([])
    mock_connect.return_value = mock_account

    client = MarketDataClient(make_settings())
    await client.get_candles("XAUUSD", "H1", from_time="2024-01-01T00:00:00Z")

    call_kwargs = mock_account.get_historical_candles.call_args
    start = call_kwargs.kwargs.get("start_time")
    assert isinstance(start, datetime)
    assert start.year == 2024


@pytest.mark.asyncio
@patch("src.ingestion.market_client.MarketDataClient._ensure_connected")
async def test_empty_response_returns_empty_list(mock_connect):
    """If MetaAPI returns no candles, get_candles returns []."""
    mock_account = _make_mock_account([])
    mock_connect.return_value = mock_account

    client = MarketDataClient(make_settings())
    candles = await client.get_candles("XAUUSD", "H4", count=5)

    assert candles == []
