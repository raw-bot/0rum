"""Unit tests for OandaClient — all HTTP calls are mocked."""

import pytest
import httpx
import respx

from src.ingestion.oanda_client import OandaClient
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


@pytest.mark.asyncio
@respx.mock
async def test_get_candles_returns_candle_list():
    """get_candles returns the candles list from OANDA response."""
    settings = make_settings()
    client = OandaClient(settings)

    mock_response = {
        "candles": [
            {"time": "2024-01-02T00:00:00Z", "mid": {"o": "2000.0", "h": "2010.0", "l": "1995.0", "c": "2005.0"}, "volume": 100, "complete": True}
        ]
    }
    respx.get("https://api-fxpractice.oanda.com/v3/instruments/XAUUSD/candles").mock(
        return_value=httpx.Response(200, json=mock_response)
    )

    candles = await client.get_candles("XAUUSD", "M15", count=1)
    assert len(candles) == 1
    assert candles[0]["time"] == "2024-01-02T00:00:00Z"


@pytest.mark.asyncio
@respx.mock
async def test_d1_granularity_mapped_to_d():
    """D1 timeframe is sent as 'D' to OANDA API."""
    settings = make_settings()
    client = OandaClient(settings)

    respx.get("https://api-fxpractice.oanda.com/v3/instruments/XAUUSD/candles").mock(
        return_value=httpx.Response(200, json={"candles": []})
    )

    await client.get_candles("XAUUSD", "D1", count=5)

    # The request must have granularity=D (not D1)
    last_request = respx.calls.last.request
    assert "granularity=D" in str(last_request.url)
    assert "granularity=D1" not in str(last_request.url)


@pytest.mark.asyncio
@respx.mock
async def test_from_time_removes_count_param():
    """When from_time is set, count is not included in request params."""
    settings = make_settings()
    client = OandaClient(settings)

    respx.get("https://api-fxpractice.oanda.com/v3/instruments/XAUUSD/candles").mock(
        return_value=httpx.Response(200, json={"candles": []})
    )

    await client.get_candles("XAUUSD", "M15", from_time="2024-01-01T00:00:00Z")

    last_request = respx.calls.last.request
    assert "count=" not in str(last_request.url)
    assert "from=" in str(last_request.url)


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises():
    """HTTPStatusError is re-raised after logging."""
    settings = make_settings()
    client = OandaClient(settings)

    respx.get("https://api-fxpractice.oanda.com/v3/instruments/XAUUSD/candles").mock(
        return_value=httpx.Response(401, json={"errorMessage": "Unauthorized"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_candles("XAUUSD", "M15", count=1)


@pytest.mark.asyncio
@respx.mock
async def test_empty_candles_returns_empty_list():
    """If OANDA returns no candles key, get_candles returns empty list."""
    settings = make_settings()
    client = OandaClient(settings)

    respx.get("https://api-fxpractice.oanda.com/v3/instruments/XAUUSD/candles").mock(
        return_value=httpx.Response(200, json={})
    )

    candles = await client.get_candles("XAUUSD", "H1", count=5)
    assert candles == []
