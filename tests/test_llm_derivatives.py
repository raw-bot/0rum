from datetime import UTC, datetime

import pytest

import orum.llm.derivatives as derivatives
from orum.llm.derivatives import BinanceUsdMPublicProvider


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
PAST_MS = int(datetime(2026, 7, 13, 11, 59, tzinfo=UTC).timestamp() * 1000)


class FakeExchange:
    def __init__(self):
        self.calls = []

    def fetch_funding_rate(self, symbol):
        self.calls.append(("funding", symbol))
        return {
            "symbol": symbol,
            "fundingRate": 0.0001,
            "fundingTimestamp": PAST_MS,
            "nextFundingTimestamp": int(datetime(2026, 7, 13, 16, tzinfo=UTC).timestamp() * 1000),
            "markPrice": 100.5,
            "indexPrice": 100.4,
        }

    def fetch_open_interest(self, symbol):
        self.calls.append(("open_interest", symbol))
        return {
            "symbol": symbol,
            "openInterestAmount": 20_000,
            "openInterestValue": 2_010_000,
            "timestamp": PAST_MS,
        }

    def fetch_ticker(self, symbol):
        self.calls.append(("ticker", symbol))
        return {"symbol": symbol, "bid": 100, "ask": 101, "last": 100.6, "timestamp": PAST_MS}

    def fetch_order_book(self, symbol, limit):
        self.calls.append(("order_book", symbol, limit))
        return {
            "symbol": symbol,
            "bids": [[100, 2], [99, 1]],
            "asks": [[101, 1], [102, 1]],
            "timestamp": PAST_MS,
        }


def test_derivatives_provider_normalizes_public_ccxt_evidence():
    exchange = FakeExchange()
    provider = BinanceUsdMPublicProvider(exchange=exchange, clock=lambda: NOW, depth_limit=20)

    snapshot = provider.fetch("BTC/USDT:USDT")

    assert snapshot.source == "binance_usdm_public"
    assert snapshot.symbol == "BTC/USDT:USDT"
    assert snapshot.observed_at == NOW
    assert snapshot.funding_at == datetime(2026, 7, 13, 11, 59, tzinfo=UTC)
    assert snapshot.next_funding_at == datetime(2026, 7, 13, 16, tzinfo=UTC)
    assert snapshot.open_interest_at == snapshot.ticker_at == snapshot.order_book_at == snapshot.funding_at
    assert snapshot.funding_rate == pytest.approx(0.0001)
    assert snapshot.open_interest_amount == 20_000
    assert snapshot.open_interest_value == 2_010_000
    assert snapshot.mark_price == 100.5
    assert snapshot.index_price == 100.4
    assert snapshot.last_price == 100.6
    assert snapshot.best_bid == 100
    assert snapshot.best_ask == 101
    assert snapshot.spread_bps == pytest.approx((1 / 100.5) * 10_000)
    assert snapshot.depth_imbalance == pytest.approx(0.2)
    assert snapshot.errors == ()
    assert exchange.calls == [
        ("funding", "BTC/USDT:USDT"),
        ("open_interest", "BTC/USDT:USDT"),
        ("ticker", "BTC/USDT:USDT"),
        ("order_book", "BTC/USDT:USDT", 20),
    ]
    assert snapshot.to_mapping()["observed_at"] == "2026-07-13T12:00:00+00:00"


def test_unavailable_endpoint_returns_partial_evidence_with_explicit_error():
    class PartialExchange(FakeExchange):
        def fetch_open_interest(self, symbol):
            raise RuntimeError("endpoint unavailable")

    snapshot = BinanceUsdMPublicProvider(
        exchange=PartialExchange(), clock=lambda: NOW
    ).fetch("BTC/USDT:USDT")

    assert snapshot.funding_rate == 0.0001
    assert snapshot.open_interest_amount is None
    assert snapshot.open_interest_value is None
    assert snapshot.errors == ("open_interest: RuntimeError: endpoint unavailable",)


def test_future_endpoint_timestamp_and_non_finite_value_are_rejected():
    class InvalidExchange(FakeExchange):
        def fetch_ticker(self, symbol):
            return {
                "symbol": symbol,
                "bid": float("nan"),
                "ask": 101,
                "last": 100,
                "timestamp": int(datetime(2026, 7, 13, 12, 1, tzinfo=UTC).timestamp() * 1000),
            }

    snapshot = BinanceUsdMPublicProvider(
        exchange=InvalidExchange(), clock=lambda: NOW
    ).fetch("BTC/USDT:USDT")

    assert snapshot.last_price is None
    assert snapshot.ticker_at is None
    assert any(error.startswith("ticker: future timestamp") for error in snapshot.errors)


def test_default_exchange_factory_uses_rate_limit_and_no_credentials(monkeypatch):
    seen = {}
    fake = FakeExchange()

    def factory(config):
        seen.update(config)
        return fake

    monkeypatch.setattr(derivatives.ccxt, "binanceusdm", factory)

    provider = BinanceUsdMPublicProvider()

    assert provider.exchange is fake
    assert seen == {"enableRateLimit": True}
