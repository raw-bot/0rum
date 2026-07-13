"""Read-only CCXT candle adapter for the paper research portfolio."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any


class CcxtClosedCandleProvider:
    """Fetch normalized, oldest-to-newest, finalized spot candles.

    CCXT documents the unified ``fetch_ohlcv`` shape and warns that the final
    current candle can be incomplete. Kraken further guarantees that its last
    OHLC row is the uncommitted timeframe, so finalization is enforced locally.

    Sources:
    - https://github.com/ccxt/ccxt/wiki/manual#ohlcv-candlestick-charts
    - https://docs.kraken.com/api-reference/market-data/get-ohlc-data
    """

    def __init__(
        self,
        exchanges: Mapping[str, Any],
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._exchanges = dict(exchanges)
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    def __call__(self, venue: str, symbol: str, timeframe: str, limit: int) -> list[dict]:
        exchange = self._exchanges.get(venue)
        if exchange is None:
            raise ValueError(f"unknown venue {venue!r}")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("candle limit must be a positive integer")
        if not exchange.has.get("fetchOHLCV"):
            raise ValueError(f"venue {venue!r} does not support OHLCV")

        markets = exchange.load_markets()
        market = markets.get(symbol)
        if not market or market.get("active") is not True or market.get("spot") is not True:
            raise ValueError(f"{symbol!r} is not an active spot market on {venue!r}")

        timeframe_ms = int(exchange.parse_timeframe(timeframe) * 1000)
        if timeframe_ms <= 0:
            raise ValueError(f"invalid timeframe {timeframe!r}")
        rows = exchange.fetch_ohlcv(
            symbol,
            timeframe,
            since=None,
            limit=limit + 1,
            params={},
        )

        normalized: list[dict] = []
        previous_ts: int | None = None
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                raise ValueError("invalid OHLCV row")
            ts = int(row[0])
            if previous_ts is not None and ts <= previous_ts:
                raise ValueError("OHLCV timestamps must be strictly increasing")
            previous_ts = ts
            open_, high, low, close = map(float, row[1:5])
            volume = 0.0 if row[5] is None else float(row[5])
            if low > high or not (low <= open_ <= high and low <= close <= high):
                raise ValueError(f"invalid OHLC at timestamp {ts}")
            normalized.append({
                "ts": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            })

        now = int(self._now_ms())
        finalized = [bar for bar in normalized if bar["ts"] + timeframe_ms <= now]
        return finalized[-limit:]
