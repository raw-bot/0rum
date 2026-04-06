"""Unit tests for CandleFetcher — OandaClient is mocked."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ingestion.candle_fetcher import CandleFetcher
from src.config import Settings


def make_settings() -> Settings:
    return Settings(
        oanda_api_key="test-key",
        oanda_account_id="test-account",
        oanda_api_url="https://api-fxpractice.oanda.com",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        telegram_bot_token="test-token",
        telegram_chat_id="test-chat",
    )


def make_raw_candle(ts: str = "2024-01-02T00:00:00Z") -> dict:
    return {
        "time": ts,
        "mid": {"o": "2000.00000", "h": "2010.00000", "l": "1995.00000", "c": "2005.00000"},
        "volume": 150,
        "complete": True,
    }


def test_parse_candle_valid():
    """_parse_candle returns a Candle ORM object for a valid raw dict."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    candle = fetcher._parse_candle(make_raw_candle(), "XAUUSD", "M15")
    assert candle is not None
    assert candle.instrument == "XAUUSD"
    assert candle.timeframe == "M15"
    assert candle.open == Decimal("2000.00000")
    assert candle.complete is True


def test_parse_candle_missing_mid_returns_none():
    """_parse_candle returns None when 'mid' key is absent."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    raw = {"time": "2024-01-02T00:00:00Z", "volume": 100, "complete": True}
    result = fetcher._parse_candle(raw, "XAUUSD", "M15")
    assert result is None


def test_parse_candle_missing_timestamp_returns_none():
    """_parse_candle returns None when 'time' key is absent."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    raw = {"mid": {"o": "1", "h": "1", "l": "1", "c": "1"}, "volume": 0, "complete": True}
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
    # Should have called get_candles exactly once then stopped
    mock_client.get_candles.assert_called_once()
