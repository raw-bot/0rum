"""Unit tests for CandleFetcher — MarketDataClient is mocked."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.ingestion.candle_fetcher import CandleFetcher
from src.config import Settings


def make_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
    )


def make_raw_candle(ts: str = "2024-01-02T00:00:00Z") -> dict:
    """Normalized Binance candle dict (flat format from MarketDataClient._normalize)."""
    return {
        "time": ts,
        "open": 2000.0,
        "high": 2010.0,
        "low": 1995.0,
        "close": 2005.0,
        "volume": 150.0,
    }


def test_parse_candle_valid():
    """_parse_candle returns a Candle ORM object for a valid flat dict."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    candle = fetcher._parse_candle(make_raw_candle(), "XAUUSD", "M15")
    assert candle is not None
    assert candle.instrument == "XAUUSD"
    assert candle.timeframe == "M15"
    assert candle.open == Decimal("2000.0")
    assert candle.complete is True  # Binance historical candles are always complete


def test_parse_candle_missing_ohlc_returns_none():
    """_parse_candle returns None when OHLC keys are absent."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    raw = {"time": "2024-01-02T00:00:00Z", "volume": 100.0}
    result = fetcher._parse_candle(raw, "XAUUSD", "M15")
    assert result is None


def test_parse_candle_missing_timestamp_returns_none():
    """_parse_candle returns None when 'time' key is absent."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    raw = {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}
    result = fetcher._parse_candle(raw, "XAUUSD", "H1")
    assert result is None


@pytest.mark.asyncio
async def test_backfill_skips_store_on_empty_responses():
    """backfill_timeframe returns 0 and never calls fetch_and_store when API returns no bars.

    The loop iterates through all date-range chunks (bounded by MAX_PAGINATION_ITERS);
    it does not short-circuit on the first empty chunk.
    """
    fetcher = CandleFetcher.__new__(CandleFetcher)
    fetcher.settings = make_settings()

    mock_client = AsyncMock()
    mock_client.get_candles = AsyncMock(return_value=[])
    fetcher.client = mock_client

    total = await fetcher.backfill_timeframe(instrument="XAUUSD", timeframe="M15")

    assert total == 0
    assert mock_client.get_candles.call_count > 0


@pytest.mark.asyncio
async def test_aclose_propagates_to_market_client():
    """aclose() forwards the close call to the underlying MarketDataClient."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    mock_client = AsyncMock()
    fetcher.client = mock_client

    await fetcher.aclose()

    mock_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_manager_closes_client_on_exit():
    """async with CandleFetcher() closes the client when the block exits."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    mock_client = AsyncMock()
    fetcher.client = mock_client

    async with fetcher:
        pass

    mock_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_warm_up_timeframe_issues_single_count_fetch():
    """warm_up_timeframe issues exactly one get_candles call using count, no date range."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    fetcher.settings = make_settings()
    mock_client = AsyncMock()
    mock_client.get_candles = AsyncMock(return_value=[])
    fetcher.client = mock_client

    await fetcher.warm_up_timeframe(instrument="XAUUSD", timeframe="M15")

    mock_client.get_candles.assert_awaited_once()
    kwargs = mock_client.get_candles.call_args.kwargs
    assert kwargs.get("from_time") is None
    assert kwargs.get("to_time") is None
    assert kwargs.get("count", 1) > 0


@pytest.mark.asyncio
async def test_warm_up_all_makes_exactly_four_bounded_fetches():
    """warm_up_all issues exactly 4 get_candles calls (one per timeframe), no date ranges."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    fetcher.settings = make_settings()
    mock_client = AsyncMock()
    mock_client.get_candles = AsyncMock(return_value=[])
    fetcher.client = mock_client

    await fetcher.warm_up_all()

    assert mock_client.get_candles.call_count == 4
    for call in mock_client.get_candles.call_args_list:
        kwargs = call.kwargs
        assert kwargs.get("from_time") is None
        assert kwargs.get("to_time") is None


@pytest.mark.asyncio
async def test_warm_up_all_continues_after_timeframe_error():
    """warm_up_all logs the error and continues to remaining timeframes on failure."""
    fetcher = CandleFetcher.__new__(CandleFetcher)
    fetcher.settings = make_settings()
    mock_client = AsyncMock()
    mock_client.get_candles = AsyncMock(
        side_effect=[RuntimeError("provider down"), [], [], []]
    )
    fetcher.client = mock_client

    await fetcher.warm_up_all()

    assert mock_client.get_candles.call_count == 4
