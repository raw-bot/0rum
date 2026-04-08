"""Unit tests for CandleFetcher — MarketDataClient is mocked."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.ingestion.candle_fetcher import CandleFetcher
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
    """Normalized MetaAPI candle dict (flat format)."""
    return {
        "time": ts,
        "open": 2000.0,
        "high": 2010.0,
        "low": 1995.0,
        "close": 2005.0,
        "tickVolume": 150,
    }


def test_parse_candle_valid():
    """_parse_candle returns a Candle ORM object for a valid flat dict."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    candle = fetcher._parse_candle(make_raw_candle(), "XAUUSD", "M15")
    assert candle is not None
    assert candle.instrument == "XAUUSD"
    assert candle.timeframe == "M15"
    assert candle.open == Decimal("2000.0")
    assert candle.complete is True  # MetaAPI historical candles are always complete


def test_parse_candle_missing_ohlc_returns_none():
    """_parse_candle returns None when OHLC keys are absent."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    raw = {"time": "2024-01-02T00:00:00Z", "tickVolume": 100}
    result = fetcher._parse_candle(raw, "XAUUSD", "M15")
    assert result is None


def test_parse_candle_missing_timestamp_returns_none():
    """_parse_candle returns None when 'time' key is absent."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    raw = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tickVolume": 0}
    result = fetcher._parse_candle(raw, "XAUUSD", "H1")
    assert result is None


@pytest.mark.asyncio
async def test_backfill_terminates_on_empty_response():
    """backfill_timeframe stops immediately if the API returns an empty list."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    fetcher.settings = make_settings()

    mock_client = AsyncMock()
    mock_client.get_candles = AsyncMock(return_value=[])
    fetcher.client = mock_client

    total = await fetcher.backfill_timeframe(instrument="XAUUSD", timeframe="M15")

    assert total == 0
    mock_client.get_candles.assert_called_once()
