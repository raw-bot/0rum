import pytest

from orum.portfolio.ccxt_provider import CcxtClosedCandleProvider


class FakeExchange:
    has = {"fetchOHLCV": True}

    def __init__(self, rows, *, market=None):
        self.rows = rows
        self.market = market or {"active": True, "spot": True}
        self.calls = []

    def load_markets(self):
        return {"BTC/EUR": self.market}

    def parse_timeframe(self, timeframe):
        assert timeframe == "4h"
        return 4

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        self.calls.append((symbol, timeframe, since, limit, params))
        return self.rows


def _row(ts, open_=100.0, high=101.0, low=99.0, close=100.5, volume=2.0):
    return [ts, open_, high, low, close, volume]


def test_provider_returns_only_finalized_normalized_bars():
    exchange = FakeExchange([_row(0), _row(4_000), _row(8_000)])
    provider = CcxtClosedCandleProvider({"kraken": exchange}, now_ms=lambda: 10_000)

    bars = provider("kraken", "BTC/EUR", "4h", 300)

    assert [bar["ts"] for bar in bars] == [0, 4_000]
    assert bars[-1] == {
        "ts": 4_000,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 2.0,
    }
    assert exchange.calls == [("BTC/EUR", "4h", None, 301, {})]


@pytest.mark.parametrize(
    "market, message",
    [
        ({"active": False, "spot": True}, "active spot"),
        ({"active": True, "spot": False}, "active spot"),
    ],
)
def test_provider_rejects_inactive_or_nonspot_market(market, message):
    provider = CcxtClosedCandleProvider(
        {"kraken": FakeExchange([_row(0)], market=market)}, now_ms=lambda: 10_000,
    )

    with pytest.raises(ValueError, match=message):
        provider("kraken", "BTC/EUR", "4h", 10)


def test_provider_rejects_unknown_venue_and_missing_ohlcv_capability():
    exchange = FakeExchange([_row(0)])
    exchange.has = {"fetchOHLCV": False}
    provider = CcxtClosedCandleProvider({"kraken": exchange}, now_ms=lambda: 10_000)

    with pytest.raises(ValueError, match="unknown venue"):
        provider("ghost", "BTC/EUR", "4h", 10)
    with pytest.raises(ValueError, match="does not support OHLCV"):
        provider("kraken", "BTC/EUR", "4h", 10)


def test_provider_rejects_duplicate_or_reversed_timestamps():
    provider = CcxtClosedCandleProvider(
        {"kraken": FakeExchange([_row(4_000), _row(4_000)])}, now_ms=lambda: 10_000,
    )

    with pytest.raises(ValueError, match="strictly increasing"):
        provider("kraken", "BTC/EUR", "4h", 10)


def test_provider_rejects_malformed_ohlc():
    provider = CcxtClosedCandleProvider(
        {"kraken": FakeExchange([_row(0, open_=105.0, high=101.0)])}, now_ms=lambda: 10_000,
    )

    with pytest.raises(ValueError, match="invalid OHLC"):
        provider("kraken", "BTC/EUR", "4h", 10)
