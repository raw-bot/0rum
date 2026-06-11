import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from hermes_trading.adapters import macro, news, onchain, price


class OfflineFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_price_fetch_returns_schema_payload_when_public_endpoint_is_unavailable(self):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
            get.side_effect = httpx.ConnectError("offline")

            payload = await price.fetch("BTC/USDT")

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["source"], "offline_fallback")
        self.assertEqual(payload["asset"], "BTC/USDT")
        self.assertGreaterEqual(len(payload["closes"]), 15)

    async def test_optional_adapters_return_schema_payloads_when_public_endpoints_are_unavailable(self):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
            get.side_effect = httpx.ConnectError("offline")

            onchain_payload = await onchain.fetch()
            macro_payload = await macro.fetch()

        self.assertEqual(onchain_payload["schema_version"], 1)
        self.assertEqual(macro_payload["schema_version"], 1)
        self.assertEqual(onchain_payload["source"], "offline_fallback")
        self.assertEqual(macro_payload["source"], "offline_fallback")

    async def test_news_without_api_key_reports_not_configured_without_http(self):
        env = {key: value for key, value in os.environ.items() if key != "NEWS_API_KEY"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get,
        ):
            payload = await news.fetch()

        get.assert_not_called()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["source"], "not_configured")
