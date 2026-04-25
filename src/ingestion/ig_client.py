"""IG demo/live API client for real XAUUSD discovery and historical candle access.

This module is intentionally additive: it does not replace the current Binance-based
MarketDataClient yet. It provides the primitives we need to validate the IG path
before Phase 5:
  - authenticate and obtain CST / X-SECURITY-TOKEN
  - inspect available accounts
  - search markets and inspect a chosen EPIC
  - fetch historical prices and normalize them to 0rum's internal candle contract
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import httpx
import structlog

from src.config import Settings, get_settings

log = structlog.get_logger(__name__)

PriceType = Literal["MID", "BID", "ASK", "LAST"]

_TIMEFRAME_MAP: dict[str, str] = {
    "M15": "MINUTE_15",
    "H1": "HOUR",
    "H4": "HOUR_4",
    "D1": "DAY",
}

_INSTRUMENT_EPIC_SETTING: dict[str, str] = {
    "XAUUSD": "ig_xauusd_epic",
}


class IGClient:
    """Async IG REST client for authentication, account inspection, and price history.

    The client keeps CST / X-SECURITY-TOKEN in memory after authentication and uses
    them on subsequent requests.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.api_key = self.settings.ig_api_key
        self.identifier = self.settings.ig_identifier
        self.password = self.settings.ig_password
        self.base_url = self.settings.ig_api_url.rstrip("/")
        self.target_account_id = self.settings.ig_account_id or None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
        )
        self._owns_client = http_client is None
        self._cst: str | None = None
        self._security_token: str | None = None
        self.current_account_id: str | None = None

    async def __aenter__(self) -> "IGClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying AsyncClient if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()

    @property
    def is_authenticated(self) -> bool:
        """Whether CST and X-SECURITY-TOKEN are currently present."""
        return bool(self._cst and self._security_token)

    def _base_headers(self, version: str) -> dict[str, str]:
        return {
            "Accept": "application/json; charset=UTF-8",
            "Content-Type": "application/json; charset=UTF-8",
            "X-IG-API-KEY": self.api_key,
            "Version": version,
        }

    def _auth_headers(self, version: str) -> dict[str, str]:
        if not self.is_authenticated:
            raise RuntimeError("IG client is not authenticated yet.")
        return {
            **self._base_headers(version),
            "CST": self._cst or "",
            "X-SECURITY-TOKEN": self._security_token or "",
        }

    @staticmethod
    def _extract_error_detail(response: httpx.Response) -> str:
        """Extract a short API error code/body fragment for raised exceptions."""
        try:
            payload = response.json()
        except ValueError:
            text = response.text.strip()
            return text[:200] if text else f"HTTP {response.status_code}"

        if isinstance(payload, dict):
            for key in ("errorCode", "error", "message"):
                value = payload.get(key)
                if value:
                    return str(value)
        return f"HTTP {response.status_code}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        version: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        authenticated: bool = True,
        _is_retry: bool = False,
    ) -> httpx.Response:
        headers = (
            self._auth_headers(version) if authenticated else self._base_headers(version)
        )
        response = await self._client.request(
            method,
            path,
            headers=headers,
            params=params,
            json=json,
        )

        # IG sessions expire (~6h on demo). On a 401, clear stale tokens,
        # re-authenticate once, and retry the original request. The _is_retry
        # flag prevents an infinite loop if the fresh session also returns 401.
        if response.status_code == 401 and authenticated and not _is_retry:
            log.info(
                "ig_client.session_expired_reauth",
                method=method,
                path=path,
            )
            self._cst = None
            self._security_token = None
            await self.authenticate()
            return await self._request(
                method,
                path,
                version=version,
                params=params,
                json=json,
                authenticated=authenticated,
                _is_retry=True,
            )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = self._extract_error_detail(response)
            log.error(
                "ig_client.request_failed",
                method=method,
                path=path,
                status_code=response.status_code,
                detail=detail,
            )
            raise RuntimeError(f"IG API request failed: {detail}") from exc
        return response

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("IG_API_KEY", self.api_key),
                ("IG_IDENTIFIER", self.identifier),
                ("IG_PASSWORD", self.password),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"Missing IG credentials in settings: {', '.join(missing)}"
            )

    async def authenticate(self) -> dict[str, Any]:
        """Create an IG session (version 2) and cache the returned security tokens."""
        self._require_credentials()

        response = await self._request(
            "POST",
            "/session",
            version="2",
            authenticated=False,
            json={
                "identifier": self.identifier,
                "password": self.password,
                "encryptedPassword": False,
            },
        )
        payload = response.json()
        self._cst = response.headers.get("CST")
        self._security_token = response.headers.get("X-SECURITY-TOKEN")
        self.current_account_id = (
            payload.get("currentAccountId")
            or payload.get("accountId")
            or self.target_account_id
        )

        if not self._cst or not self._security_token:
            raise RuntimeError("IG session created but CST/X-SECURITY-TOKEN were missing.")

        if self.target_account_id and self.current_account_id != self.target_account_id:
            await self.switch_account(self.target_account_id)

        account_count = len(payload.get("accounts", []))
        log.info(
            "ig_client.authenticated",
            current_account_id=self.current_account_id,
            account_count=account_count,
            has_demo_accounts=payload.get("hasActiveDemoAccounts"),
        )
        return payload

    async def ensure_authenticated(self) -> None:
        """Authenticate lazily when the client is first used."""
        if not self.is_authenticated:
            await self.authenticate()

    async def switch_account(
        self, account_id: str, *, default_account: bool = False
    ) -> dict[str, Any]:
        """Switch the active IG account after authentication."""
        await self.ensure_authenticated()
        response = await self._request(
            "PUT",
            "/session",
            version="1",
            json={"accountId": account_id, "defaultAccount": default_account},
        )
        self.current_account_id = account_id
        log.info("ig_client.account_switched", account_id=account_id)
        return response.json()

    async def get_accounts(self) -> list[dict[str, Any]]:
        """Return the list of accounts available to the authenticated user."""
        await self.ensure_authenticated()
        response = await self._request("GET", "/accounts", version="1")
        payload = response.json()
        accounts = payload.get("accounts", [])
        log.info("ig_client.accounts_loaded", account_count=len(accounts))
        return accounts

    async def search_markets(self, search_term: str) -> list[dict[str, Any]]:
        """Search IG markets by term, e.g. ``gold``."""
        await self.ensure_authenticated()
        response = await self._request(
            "GET",
            "/markets",
            version="1",
            params={"searchTerm": search_term},
        )
        payload = response.json()
        markets = payload.get("markets", [])
        log.info(
            "ig_client.markets_searched",
            search_term=search_term,
            market_count=len(markets),
        )
        return markets

    async def get_market(self, epic: str) -> dict[str, Any]:
        """Fetch detailed IG market metadata for one EPIC."""
        await self.ensure_authenticated()
        response = await self._request("GET", f"/markets/{epic}", version="4")
        payload = response.json()
        instrument_type = payload.get("instrument", {}).get("type")
        log.info(
            "ig_client.market_loaded",
            epic=epic,
            instrument_type=instrument_type,
            market_status=payload.get("snapshot", {}).get("marketStatus"),
        )
        return payload

    async def get_prices(
        self,
        epic: str,
        resolution: str,
        *,
        num_points: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch raw IG price bars via the IG v3 prices endpoint.

        IG exposes several historical-prices routes, but the most reliable one for
        our demo CFD account is ``GET /prices/{epic}`` (v3) with query parameters.
        This avoids the older path variants that returned ``404 invalid.url`` during
        live probing on the selected gold EPIC.
        """
        await self.ensure_authenticated()

        if num_points is not None and (start_date or end_date):
            raise ValueError("Use either num_points or start/end date range, not both.")

        params: dict[str, Any] = {
            "resolution": resolution,
            # Disable paging so the response contains the full requested slice.
            "pageSize": 0,
        }

        if num_points is not None:
            params["max"] = num_points
        else:
            if not start_date:
                raise ValueError("start_date is required when num_points is omitted.")
            params["from"] = self._format_ig_datetime(start_date)
            params["to"] = self._format_ig_datetime(
                end_date or datetime.now(timezone.utc).isoformat()
            )

        response = await self._request(
            "GET",
            f"/prices/{epic}",
            version="3",
            params=params,
        )

        payload = response.json()
        return payload.get("prices", [])

    async def get_candles(
        self,
        instrument: str = "XAUUSD",
        granularity: str = "M15",
        *,
        count: int = 500,
        from_time: str | None = None,
        to_time: str | None = None,
        epic: str | None = None,
        price_type: PriceType = "MID",
    ) -> list[dict[str, Any]]:
        """Fetch IG prices and normalize them to 0rum's internal candle contract."""
        resolution = _TIMEFRAME_MAP.get(granularity, granularity)
        resolved_epic = epic or self._resolve_epic(instrument)

        prices = await self.get_prices(
            resolved_epic,
            resolution,
            num_points=None if from_time else count,
            start_date=from_time,
            end_date=to_time,
        )
        normalized = [
            candle
            for price in prices
            if (candle := self._normalize_price_bar(price, price_type=price_type))
            is not None
        ]
        log.info(
            "ig_client.candles_fetched",
            instrument=instrument,
            epic=resolved_epic,
            granularity=granularity,
            count=len(normalized),
            from_time=from_time,
        )
        return normalized

    def _resolve_epic(self, instrument: str) -> str:
        if instrument not in _INSTRUMENT_EPIC_SETTING:
            raise KeyError(f"No IG epic mapping configured for instrument '{instrument}'.")

        setting_name = _INSTRUMENT_EPIC_SETTING[instrument]
        epic = getattr(self.settings, setting_name)
        if not epic:
            raise RuntimeError(
                f"Missing IG epic for {instrument}. Set {setting_name.upper()} in .env."
            )
        return epic

    @staticmethod
    def _format_ig_datetime(raw_value: str) -> str:
        """Format a timestamp for IG v3 prices query params.

        IG v3 expects ``yyyy-MM-dd'T'HH:mm:ss`` without a trailing timezone marker.
        We treat naive inputs as UTC and normalize aware datetimes to UTC before
        stripping the timezone offset.
        """
        cleaned = raw_value.strip()
        if cleaned.endswith("Z"):
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        elif "T" in cleaned and ("+" in cleaned[10:] or "-" in cleaned[10:]):
            dt = datetime.fromisoformat(cleaned)
        else:
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _normalize_timestamp(raw_timestamp: str) -> str:
        cleaned = raw_timestamp.strip()
        if cleaned.endswith("Z"):
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        elif "T" in cleaned and ("+" in cleaned[10:] or "-" in cleaned[10:]):
            dt = datetime.fromisoformat(cleaned)
        else:
            dt = datetime.fromisoformat(f"{cleaned}+00:00")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _extract_price_component(
        price_block: dict[str, Any],
        *,
        price_type: PriceType,
    ) -> float | None:
        bid = price_block.get("bid")
        ask = price_block.get("ask")
        last = price_block.get("lastTraded")

        if price_type == "MID":
            if bid is not None and ask is not None:
                return (float(bid) + float(ask)) / 2.0
            if last is not None:
                return float(last)
            if bid is not None:
                return float(bid)
            if ask is not None:
                return float(ask)
            return None

        if price_type == "BID":
            return float(bid) if bid is not None else None
        if price_type == "ASK":
            return float(ask) if ask is not None else None
        if price_type == "LAST":
            return float(last) if last is not None else None

        raise ValueError(f"Unsupported price_type '{price_type}'.")

    @classmethod
    def _normalize_price_bar(
        cls,
        price_bar: dict[str, Any],
        *,
        price_type: PriceType,
    ) -> dict[str, Any] | None:
        """Normalize one IG historical price bar to the candle dict used by 0rum."""
        timestamp = price_bar.get("snapshotTimeUTC") or price_bar.get("snapshotTime")
        if not timestamp:
            return None

        open_price = cls._extract_price_component(
            price_bar.get("openPrice", {}),
            price_type=price_type,
        )
        high_price = cls._extract_price_component(
            price_bar.get("highPrice", {}),
            price_type=price_type,
        )
        low_price = cls._extract_price_component(
            price_bar.get("lowPrice", {}),
            price_type=price_type,
        )
        close_price = cls._extract_price_component(
            price_bar.get("closePrice", {}),
            price_type=price_type,
        )

        if None in (open_price, high_price, low_price, close_price):
            return None

        volume = price_bar.get("lastTradedVolume") or 0

        return {
            "time": cls._normalize_timestamp(str(timestamp)),
            "open": float(open_price),
            "high": float(high_price),
            "low": float(low_price),
            "close": float(close_price),
            "volume": int(volume),
        }
