"""Read-only Binance USD-M perpetual evidence normalized from CCXT."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import ccxt


# Unified method signatures and structures verified against CCXT 4.5.56:
# https://github.com/ccxt/ccxt/wiki/manual#funding-rate
# https://github.com/ccxt/ccxt/wiki/manual#open-interest
# https://github.com/ccxt/ccxt/wiki/manual#price-tickers
# https://github.com/ccxt/ccxt/wiki/manual#order-book


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class DerivativesSnapshot:
    source: str
    symbol: str
    observed_at: datetime
    funding_at: datetime | None
    next_funding_at: datetime | None
    open_interest_at: datetime | None
    ticker_at: datetime | None
    order_book_at: datetime | None
    funding_rate: float | None
    open_interest_amount: float | None
    open_interest_value: float | None
    mark_price: float | None
    index_price: float | None
    last_price: float | None
    best_bid: float | None
    best_ask: float | None
    spread_bps: float | None
    depth_imbalance: float | None
    errors: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "source": self.source,
            "symbol": self.symbol,
            "observed_at": _iso(self.observed_at),
            "funding_at": _iso(self.funding_at),
            "next_funding_at": _iso(self.next_funding_at),
            "open_interest_at": _iso(self.open_interest_at),
            "ticker_at": _iso(self.ticker_at),
            "order_book_at": _iso(self.order_book_at),
            "funding_rate": self.funding_rate,
            "open_interest_amount": self.open_interest_amount,
            "open_interest_value": self.open_interest_value,
            "mark_price": self.mark_price,
            "index_price": self.index_price,
            "last_price": self.last_price,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread_bps": self.spread_bps,
            "depth_imbalance": self.depth_imbalance,
            "errors": list(self.errors),
        }


class _EvidenceError(ValueError):
    pass


def _finite(value: object, name: str, *, positive: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _EvidenceError(f"{name} is not finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _EvidenceError(f"{name} is not finite") from exc
    if not math.isfinite(number):
        raise _EvidenceError(f"{name} is not finite")
    if positive and number <= 0:
        raise _EvidenceError(f"{name} must be positive")
    return number


def _timestamp_ms(value: object, name: str, now: datetime, *, allow_future: bool = False) -> datetime | None:
    if value is None:
        return None
    milliseconds = _finite(value, name)
    assert milliseconds is not None
    if milliseconds < 0:
        raise _EvidenceError(f"{name} must be non-negative")
    result = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    if not allow_future and result > now + timedelta(seconds=5):
        raise _EvidenceError(f"future timestamp {result.isoformat()}")
    return result


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _EvidenceError(f"{name} response is not an object")
    return value


class BinanceUsdMPublicProvider:
    """Fetch only CCXT public market endpoints; never balances or orders."""

    def __init__(
        self,
        exchange: Any | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
        depth_limit: int = 20,
    ) -> None:
        if isinstance(depth_limit, bool) or not isinstance(depth_limit, int) or depth_limit <= 0:
            raise ValueError("depth_limit must be a positive integer")
        # binanceusdm is CCXT's dedicated Binance USDⓈ-M exchange class. No API
        # key/secret is configured because every method below is public.
        # https://github.com/ccxt/ccxt#supported-cryptocurrency-exchange-markets
        self.exchange = exchange or ccxt.binanceusdm({"enableRateLimit": True})
        self._clock = clock
        self.depth_limit = depth_limit

    def fetch(self, symbol: str) -> DerivativesSnapshot:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be non-empty")
        observed_at = self._clock().astimezone(UTC)
        errors: list[str] = []

        funding = self._call(
            "funding",
            lambda: self.exchange.fetch_funding_rate(symbol),
            errors,
        )
        open_interest = self._call(
            "open_interest",
            lambda: self.exchange.fetch_open_interest(symbol),
            errors,
        )
        ticker = self._call(
            "ticker",
            lambda: self.exchange.fetch_ticker(symbol),
            errors,
        )
        order_book = self._call(
            "order_book",
            lambda: self.exchange.fetch_order_book(symbol, self.depth_limit),
            errors,
        )

        funding_values = self._normalize_funding(funding, observed_at, errors)
        open_interest_values = self._normalize_open_interest(open_interest, observed_at, errors)
        ticker_values = self._normalize_ticker(ticker, observed_at, errors)
        order_book_values = self._normalize_order_book(order_book, observed_at, errors)

        best_bid = order_book_values.get("best_bid", ticker_values.get("best_bid"))
        best_ask = order_book_values.get("best_ask", ticker_values.get("best_ask"))
        spread_bps = self._spread_bps(best_bid, best_ask)
        return DerivativesSnapshot(
            source="binance_usdm_public",
            symbol=symbol.strip(),
            observed_at=observed_at,
            funding_at=funding_values.get("funding_at"),
            next_funding_at=funding_values.get("next_funding_at"),
            open_interest_at=open_interest_values.get("open_interest_at"),
            ticker_at=ticker_values.get("ticker_at"),
            order_book_at=order_book_values.get("order_book_at"),
            funding_rate=funding_values.get("funding_rate"),
            open_interest_amount=open_interest_values.get("open_interest_amount"),
            open_interest_value=open_interest_values.get("open_interest_value"),
            mark_price=funding_values.get("mark_price"),
            index_price=funding_values.get("index_price"),
            last_price=ticker_values.get("last_price"),
            best_bid=best_bid,
            best_ask=best_ask,
            spread_bps=spread_bps,
            depth_imbalance=order_book_values.get("depth_imbalance"),
            errors=tuple(errors),
        )

    @staticmethod
    def _call(name: str, operation: Callable[[], object], errors: list[str]) -> Mapping[str, Any] | None:
        try:
            return _mapping(operation(), name)
        except Exception as exc:  # noqa: BLE001 - each independent public endpoint degrades explicitly
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            return None

    @staticmethod
    def _normalize_funding(
        value: Mapping[str, Any] | None,
        now: datetime,
        errors: list[str],
    ) -> dict[str, Any]:
        if value is None:
            return {}
        try:
            return {
                "funding_at": _timestamp_ms(
                    value.get("fundingTimestamp", value.get("timestamp")),
                    "funding timestamp",
                    now,
                ),
                "next_funding_at": _timestamp_ms(
                    value.get("nextFundingTimestamp"),
                    "next funding timestamp",
                    now,
                    allow_future=True,
                ),
                "funding_rate": _finite(value.get("fundingRate"), "fundingRate"),
                "mark_price": _finite(value.get("markPrice"), "markPrice", positive=True),
                "index_price": _finite(value.get("indexPrice"), "indexPrice", positive=True),
            }
        except _EvidenceError as exc:
            errors.append(f"funding: {exc}")
            return {}

    @staticmethod
    def _normalize_open_interest(
        value: Mapping[str, Any] | None,
        now: datetime,
        errors: list[str],
    ) -> dict[str, Any]:
        if value is None:
            return {}
        try:
            return {
                "open_interest_at": _timestamp_ms(value.get("timestamp"), "open interest timestamp", now),
                "open_interest_amount": _finite(
                    value.get("openInterestAmount"), "openInterestAmount"
                ),
                "open_interest_value": _finite(
                    value.get("openInterestValue"), "openInterestValue"
                ),
            }
        except _EvidenceError as exc:
            errors.append(f"open_interest: {exc}")
            return {}

    @staticmethod
    def _normalize_ticker(
        value: Mapping[str, Any] | None,
        now: datetime,
        errors: list[str],
    ) -> dict[str, Any]:
        if value is None:
            return {}
        try:
            ticker_at = _timestamp_ms(value.get("timestamp"), "ticker timestamp", now)
            return {
                "ticker_at": ticker_at,
                "best_bid": _finite(value.get("bid"), "ticker bid", positive=True),
                "best_ask": _finite(value.get("ask"), "ticker ask", positive=True),
                "last_price": _finite(value.get("last"), "ticker last", positive=True),
            }
        except _EvidenceError as exc:
            errors.append(f"ticker: {exc}")
            return {}

    @staticmethod
    def _normalize_order_book(
        value: Mapping[str, Any] | None,
        now: datetime,
        errors: list[str],
    ) -> dict[str, Any]:
        if value is None:
            return {}
        try:
            order_book_at = _timestamp_ms(value.get("timestamp"), "order book timestamp", now)
            bids = BinanceUsdMPublicProvider._levels(value.get("bids"), "bids")
            asks = BinanceUsdMPublicProvider._levels(value.get("asks"), "asks")
            if not bids or not asks:
                raise _EvidenceError("order book must contain bids and asks")
            bid_depth = math.fsum(amount for _, amount in bids)
            ask_depth = math.fsum(amount for _, amount in asks)
            total_depth = bid_depth + ask_depth
            imbalance = None if total_depth == 0 else (bid_depth - ask_depth) / total_depth
            return {
                "order_book_at": order_book_at,
                "best_bid": bids[0][0],
                "best_ask": asks[0][0],
                "depth_imbalance": imbalance,
            }
        except _EvidenceError as exc:
            errors.append(f"order_book: {exc}")
            return {}

    @staticmethod
    def _levels(value: object, name: str) -> list[tuple[float, float]]:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise _EvidenceError(f"{name} must be a list")
        result = []
        for row in value:
            if isinstance(row, (str, bytes)) or not isinstance(row, Sequence) or len(row) < 2:
                raise _EvidenceError(f"{name} contains an invalid level")
            price = _finite(row[0], f"{name} price", positive=True)
            amount = _finite(row[1], f"{name} amount")
            assert price is not None and amount is not None
            if amount < 0:
                raise _EvidenceError(f"{name} amount must be non-negative")
            result.append((price, amount))
        return result

    @staticmethod
    def _spread_bps(best_bid: float | None, best_ask: float | None) -> float | None:
        if best_bid is None or best_ask is None:
            return None
        if best_ask < best_bid:
            return None
        midpoint = (best_bid + best_ask) / 2
        return None if midpoint <= 0 else ((best_ask - best_bid) / midpoint) * 10_000
