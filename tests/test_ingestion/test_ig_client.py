"""Unit tests for the additive IG client."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.config import Settings
from src.ingestion.ig_client import IGClient


def _settings() -> Settings:
    """Build a Settings object with IG demo values for isolated tests."""
    return Settings(
        database_url="sqlite+aiosqlite:///./test.db",
        redis_url="redis://localhost:6379/0",
        telegram_bot_token="token",
        telegram_chat_id="chat",
        ig_api_key="demo-key",
        ig_identifier="demo-user",
        ig_password="demo-pass",
        ig_account_id="ABC123",
        ig_api_url="https://demo-api.ig.com/gateway/deal",
        ig_xauusd_epic="CS.D.XAUUSD.CFD.IP",
    )


def _session_payload() -> dict:
    return {
        "currentAccountId": "ABC123",
        "accounts": [
            {
                "accountId": "ABC123",
                "accountType": "CFD",
                "preferred": True,
            }
        ],
        "hasActiveDemoAccounts": True,
    }


@pytest.mark.asyncio
@respx.mock
async def test_authenticate_stores_tokens_and_account_id():
    """authenticate() stores CST/X-SECURITY-TOKEN and current_account_id."""
    route = respx.post("https://demo-api.ig.com/gateway/deal/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"},
            json=_session_payload(),
        )
    )

    async with IGClient(settings=_settings()) as client:
        payload = await client.authenticate()

        assert payload["currentAccountId"] == "ABC123"
        assert client.is_authenticated is True
        assert client.current_account_id == "ABC123"

    assert route.called
    request = route.calls.last.request
    assert request.headers["X-IG-API-KEY"] == "demo-key"
    assert request.headers["Version"] == "2"


@pytest.mark.asyncio
@respx.mock
async def test_get_accounts_uses_auth_headers_after_login():
    """get_accounts() reuses CST/X-SECURITY-TOKEN after authenticate()."""
    respx.post("https://demo-api.ig.com/gateway/deal/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"},
            json=_session_payload(),
        )
    )
    accounts_route = respx.get("https://demo-api.ig.com/gateway/deal/accounts").mock(
        return_value=httpx.Response(
            200,
            json={"accounts": [{"accountId": "ABC123", "accountType": "CFD"}]},
        )
    )

    async with IGClient(settings=_settings()) as client:
        accounts = await client.get_accounts()

        assert accounts == [{"accountId": "ABC123", "accountType": "CFD"}]

    request = accounts_route.calls.last.request
    assert request.headers["CST"] == "cst-token"
    assert request.headers["X-SECURITY-TOKEN"] == "sec-token"
    assert request.headers["Version"] == "1"


@pytest.mark.asyncio
@respx.mock
async def test_search_markets_sends_searchterm_query_param():
    """search_markets() calls /markets with searchTerm query param."""
    respx.post("https://demo-api.ig.com/gateway/deal/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"},
            json=_session_payload(),
        )
    )
    markets_route = respx.get("https://demo-api.ig.com/gateway/deal/markets").mock(
        return_value=httpx.Response(
            200,
            json={"markets": [{"instrumentName": "Gold", "epic": "CS.D.XAUUSD.CFD.IP"}]},
        )
    )

    async with IGClient(settings=_settings()) as client:
        markets = await client.search_markets("gold")

        assert markets[0]["epic"] == "CS.D.XAUUSD.CFD.IP"

    assert markets_route.calls.last.request.url.params["searchTerm"] == "gold"


@pytest.mark.asyncio
@respx.mock
async def test_get_candles_normalizes_midpoint_prices():
    """get_candles() returns 0rum-style candle dicts using bid/ask midpoints."""
    respx.post("https://demo-api.ig.com/gateway/deal/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"},
            json=_session_payload(),
        )
    )
    prices_route = respx.get(
        "https://demo-api.ig.com/gateway/deal/prices/CS.D.XAUUSD.CFD.IP"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "snapshotTimeUTC": "2026-04-13T10:15:00",
                        "openPrice": {"bid": 2000.0, "ask": 2001.0},
                        "highPrice": {"bid": 2005.0, "ask": 2006.0},
                        "lowPrice": {"bid": 1998.0, "ask": 1999.0},
                        "closePrice": {"bid": 2004.0, "ask": 2005.0},
                        "lastTradedVolume": 12,
                    }
                ]
            },
        )
    )

    async with IGClient(settings=_settings()) as client:
        candles = await client.get_candles("XAUUSD", "M15", count=2)

        assert candles == [
            {
                "time": "2026-04-13T10:15:00Z",
                "open": 2000.5,
                "high": 2005.5,
                "low": 1998.5,
                "close": 2004.5,
                "volume": 12,
            }
        ]

    assert prices_route.called
    params = prices_route.calls.last.request.url.params
    assert params["resolution"] == "MINUTE_15"
    assert params["max"] == "2"
    assert params["pageSize"] == "0"


@pytest.mark.asyncio
@respx.mock
async def test_get_candles_with_from_time_uses_date_range_endpoint():
    """from_time switches the client to IG v3 query params on /prices/{epic}."""
    respx.post("https://demo-api.ig.com/gateway/deal/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "cst-token", "X-SECURITY-TOKEN": "sec-token"},
            json=_session_payload(),
        )
    )
    route = respx.get(
        "https://demo-api.ig.com/gateway/deal/prices/CS.D.XAUUSD.CFD.IP"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "snapshotTimeUTC": "2026-04-13T10:00:00",
                        "openPrice": {"bid": 2000.0, "ask": 2002.0},
                        "highPrice": {"bid": 2004.0, "ask": 2006.0},
                        "lowPrice": {"bid": 1999.0, "ask": 2001.0},
                        "closePrice": {"bid": 2003.0, "ask": 2005.0},
                    }
                ]
            },
        )
    )

    async with IGClient(settings=_settings()) as client:
        candles = await client.get_candles(
            "XAUUSD",
            "H1",
            from_time="2026-04-01T00:00:00Z",
        )

        assert candles[0]["time"] == "2026-04-13T10:00:00Z"

    params = route.calls.last.request.url.params
    assert params["resolution"] == "HOUR"
    assert params["from"] == "2026-04-01T00:00:00"
    assert "to" in params
    assert params["pageSize"] == "0"


def test_format_ig_datetime_normalizes_utc_inputs():
    """IG query params should be formatted without the trailing timezone suffix."""
    assert IGClient._format_ig_datetime("2026-04-01T00:00:00Z") == "2026-04-01T00:00:00"
    assert IGClient._format_ig_datetime("2026-04-01T01:00:00+01:00") == "2026-04-01T00:00:00"


@pytest.mark.asyncio
async def test_get_candles_raises_when_xauusd_epic_is_missing():
    """A missing IG epic should fail loudly before any network request."""
    settings = _settings()
    settings.ig_xauusd_epic = ""

    async with IGClient(settings=settings) as client:
        with pytest.raises(RuntimeError, match="Missing IG epic"):
            await client.get_candles("XAUUSD", "M15", count=2)


def _price_bar(ts: str = "2026-04-13T10:15:00") -> dict:
    return {
        "snapshotTimeUTC": ts,
        "openPrice": {"bid": 3000.0, "ask": 3001.0},
        "highPrice": {"bid": 3005.0, "ask": 3006.0},
        "lowPrice": {"bid": 2998.0, "ask": 2999.0},
        "closePrice": {"bid": 3002.0, "ask": 3003.0},
        "lastTradedVolume": 5,
    }


@pytest.mark.asyncio
@respx.mock
async def test_session_expired_triggers_reauth_and_retry():
    """On a 401 response, the client clears tokens, re-authenticates, and retries once."""
    prices_route = respx.get(
        "https://demo-api.ig.com/gateway/deal/prices/CS.D.XAUUSD.CFD.IP"
    ).mock(
        side_effect=[
            httpx.Response(401, json={"errorCode": "error.security.client-token-invalid"}),
            httpx.Response(200, json={"prices": [_price_bar()], "instrumentType": "CURRENCIES"}),
        ]
    )
    respx.post("https://demo-api.ig.com/gateway/deal/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "fresh-cst", "X-SECURITY-TOKEN": "fresh-sec"},
            json=_session_payload(),
        )
    )

    async with IGClient(settings=_settings()) as client:
        # Seed stale tokens so ensure_authenticated() doesn't re-auth before the call
        client._cst = "stale-cst"
        client._security_token = "stale-sec"

        candles = await client.get_candles("XAUUSD", "M15", count=2)

    assert len(candles) == 1
    assert client._cst == "fresh-cst"
    assert client._security_token == "fresh-sec"
    assert prices_route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_reauth_still_401_raises_without_infinite_loop():
    """If the retried request also returns 401, RuntimeError is raised (no loop)."""
    prices_route = respx.get(
        "https://demo-api.ig.com/gateway/deal/prices/CS.D.XAUUSD.CFD.IP"
    ).mock(
        return_value=httpx.Response(401, json={"errorCode": "error.security.client-token-invalid"})
    )
    respx.post("https://demo-api.ig.com/gateway/deal/session").mock(
        return_value=httpx.Response(
            200,
            headers={"CST": "fresh-cst", "X-SECURITY-TOKEN": "fresh-sec"},
            json=_session_payload(),
        )
    )

    async with IGClient(settings=_settings()) as client:
        client._cst = "stale-cst"
        client._security_token = "stale-sec"

        with pytest.raises(RuntimeError):
            await client.get_candles("XAUUSD", "M15", count=2)

    # Original request + exactly one retry — no further attempts
    assert prices_route.call_count == 2
