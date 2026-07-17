"""Bounded public Coinbase Exchange OHLCV history retrieval.

Coinbase documents a maximum of 300 candles per request and warns that buckets
without trades may be absent. This adapter therefore pages explicit daily
windows and never fabricates missing candles.

Source:
https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
"""

from __future__ import annotations

import math
import time
from typing import Any

DAY_MS = 86_400_000
MAX_CANDLES_PER_REQUEST = 300


def _normalize_row(row: Any) -> dict:
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        raise ValueError("invalid OHLCV row")
    ts = int(row[0])
    if ts % DAY_MS:
        raise ValueError(f"invalid daily timestamp {ts}")
    open_, high, low, close = map(float, row[1:5])
    volume = 0.0 if row[5] is None else float(row[5])
    if low > high or not (low <= open_ <= high and low <= close <= high):
        raise ValueError(f"invalid OHLC at timestamp {ts}")
    return {
        "ts": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def fetch_daily_history(
    exchange: Any,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    now_ms: int | None = None,
) -> list[dict]:
    """Return unique finalized daily candles in ``[start_ms, end_ms)``."""
    if (
        not isinstance(start_ms, int)
        or isinstance(start_ms, bool)
        or not isinstance(end_ms, int)
        or isinstance(end_ms, bool)
        or start_ms >= end_ms
    ):
        raise ValueError("start_ms must be before end_ms")
    if not getattr(exchange, "has", {}).get("fetchOHLCV"):
        raise ValueError("exchange does not support OHLCV")

    markets = exchange.load_markets()
    market = markets.get(symbol)
    if not market or market.get("active") is not True or market.get("spot") is not True:
        raise ValueError(f"{symbol!r} is not an active spot market")
    if int(exchange.parse_timeframe("1d") * 1000) != DAY_MS:
        raise ValueError("invalid exchange daily timeframe")

    current_ms = time.time_ns() // 1_000_000 if now_ms is None else int(now_ms)
    by_timestamp: dict[int, dict] = {}
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(end_ms, cursor + MAX_CANDLES_PER_REQUEST * DAY_MS)
        limit = max(1, math.ceil((window_end - cursor) / DAY_MS))
        rows = exchange.fetch_ohlcv(
            symbol,
            "1d",
            since=cursor,
            limit=limit,
            params={},
        )
        previous_page_ts: int | None = None
        for row in rows:
            candle = _normalize_row(row)
            ts = candle["ts"]
            if previous_page_ts is not None and ts <= previous_page_ts:
                raise ValueError("invalid non-increasing OHLCV page")
            previous_page_ts = ts
            if not start_ms <= ts < end_ms or ts + DAY_MS > current_ms:
                continue
            existing = by_timestamp.get(ts)
            if existing is not None and existing != candle:
                raise ValueError(f"conflicting duplicate candle at timestamp {ts}")
            by_timestamp[ts] = candle
        cursor = window_end

    return [by_timestamp[ts] for ts in sorted(by_timestamp)]
