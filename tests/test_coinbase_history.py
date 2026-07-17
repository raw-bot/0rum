from __future__ import annotations

import pytest

from orum.research.coinbase_history import DAY_MS, fetch_daily_history


def _row(day: int, close: float = 100.0) -> list[float]:
    return [day * DAY_MS, close - 1.0, close + 2.0, close - 2.0, close, 10.0]


class FakeExchange:
    has = {"fetchOHLCV": True}

    def __init__(self, pages: dict[int, list[list[float]]], *, market: dict | None = None):
        self.pages = pages
        self.market = market or {"active": True, "spot": True}
        self.calls: list[tuple] = []

    def load_markets(self) -> dict:
        return {"ETH/EUR": self.market}

    def parse_timeframe(self, timeframe: str) -> int:
        assert timeframe == "1d"
        return 86_400

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        self.calls.append((symbol, timeframe, since, limit, params))
        return self.pages.get(int(since), [])


def test_fetch_daily_history_paginates_in_bounded_windows_and_filters_boundaries():
    exchange = FakeExchange({
        0: [_row(0), _row(299)],
        300 * DAY_MS: [_row(299), _row(300), _row(599)],
        600 * DAY_MS: [_row(600), _row(649), _row(650)],
    })

    candles = fetch_daily_history(
        exchange,
        "ETH/EUR",
        start_ms=0,
        end_ms=650 * DAY_MS,
        now_ms=800 * DAY_MS,
    )

    assert [bar["ts"] // DAY_MS for bar in candles] == [0, 299, 300, 599, 600, 649]
    assert [(call[2] // DAY_MS, call[3]) for call in exchange.calls] == [
        (0, 300),
        (300, 300),
        (600, 50),
    ]
    assert all(call[0:2] == ("ETH/EUR", "1d") and call[4] == {} for call in exchange.calls)


def test_fetch_daily_history_removes_incomplete_tail_and_preserves_real_gaps():
    exchange = FakeExchange({0: [_row(0), _row(2), _row(3)]})

    candles = fetch_daily_history(
        exchange,
        "ETH/EUR",
        start_ms=0,
        end_ms=4 * DAY_MS,
        now_ms=int(3.5 * DAY_MS),
    )

    assert [bar["ts"] // DAY_MS for bar in candles] == [0, 2]


def test_fetch_daily_history_rejects_conflicting_overlap():
    exchange = FakeExchange({
        0: [_row(299, 100.0)],
        300 * DAY_MS: [_row(299, 101.0), _row(300, 101.0)],
    })

    with pytest.raises(ValueError, match="conflicting duplicate"):
        fetch_daily_history(
            exchange,
            "ETH/EUR",
            start_ms=0,
            end_ms=301 * DAY_MS,
            now_ms=400 * DAY_MS,
        )


@pytest.mark.parametrize(
    ("market", "has_ohlcv", "message"),
    [
        ({"active": False, "spot": True}, True, "active spot"),
        ({"active": True, "spot": False}, True, "active spot"),
        ({"active": True, "spot": True}, False, "does not support OHLCV"),
    ],
)
def test_fetch_daily_history_rejects_unsupported_markets(market, has_ohlcv, message):
    exchange = FakeExchange({}, market=market)
    exchange.has = {"fetchOHLCV": has_ohlcv}

    with pytest.raises(ValueError, match=message):
        fetch_daily_history(exchange, "ETH/EUR", start_ms=0, end_ms=DAY_MS, now_ms=2 * DAY_MS)


@pytest.mark.parametrize(
    "rows",
    [
        [[0, 100.0, 99.0, 98.0, 100.0, 1.0]],
        [[0, 100.0, 102.0, 101.0, 100.0, 1.0]],
        [[123, 100.0, 102.0, 98.0, 100.0, 1.0]],
        [[0, 100.0, 102.0]],
    ],
)
def test_fetch_daily_history_rejects_malformed_daily_ohlcv(rows):
    exchange = FakeExchange({0: rows})

    with pytest.raises(ValueError, match="invalid"):
        fetch_daily_history(exchange, "ETH/EUR", start_ms=0, end_ms=DAY_MS, now_ms=2 * DAY_MS)


def test_fetch_daily_history_validates_requested_range():
    exchange = FakeExchange({})

    with pytest.raises(ValueError, match="start_ms must be before end_ms"):
        fetch_daily_history(exchange, "ETH/EUR", start_ms=DAY_MS, end_ms=DAY_MS, now_ms=2 * DAY_MS)
