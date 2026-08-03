"""Closed US-equity candles for paper research.

Yahoo/yfinance is intentionally classified as a paper-research source, never
as broker-grade market data. Every returned candle is complete, regular-session
only, ordered oldest-to-newest, and validated before it can reach a strategy.
"""

from __future__ import annotations

from datetime import datetime, time as wall_time, timezone
from math import isfinite
from threading import Lock
import time
from zoneinfo import ZoneInfo

import yfinance as yf

NEW_YORK = ZoneInfo("America/New_York")
SOURCE_NAME = "Yahoo Finance via yfinance"
SOURCE_GRADE = "PAPER_RESEARCH"
_SPECS = {
    "5m": {"period": "5d", "interval": "5m", "duration_ms": 300_000},
    "1d": {"period": "1mo", "interval": "1d", "duration_ms": 86_400_000},
}
_CACHE_TTL_SECONDS = 60.0
_cache_lock = Lock()
_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}


class UsEquityDataError(RuntimeError):
    """Raised when the source is unavailable or violates the candle contract."""


def _download(symbol: str, *, period: str, interval: str):
    # Official yfinance API: download supports 5m and 1d intervals. Intraday
    # history is limited to the latest 60 days, which is enough for this live
    # paper detector but not for its eventual long-history backtest.
    # Source: https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
    return yf.download(
        symbol,
        period=period,
        interval=interval,
        auto_adjust=False,
        prepost=False,
        progress=False,
        threads=False,
        timeout=10,
    )


def clear_us_equity_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _as_utc(timestamp) -> datetime:
    value = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
    if not isinstance(value, datetime):
        raise UsEquityDataError("candle timestamp is not a datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=NEW_YORK)
    return value.astimezone(timezone.utc)


def _normalize(frame, *, timeframe: str, now: datetime) -> list[dict]:
    if frame is None or frame.empty:
        raise UsEquityDataError("source returned no candles")
    data = frame.copy()
    if getattr(data.columns, "nlevels", 1) > 1:
        data.columns = data.columns.get_level_values(0)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(set(data.columns)):
        raise UsEquityDataError("source candle columns are incomplete")

    now_utc = now.astimezone(timezone.utc)
    today_ny = now_utc.astimezone(NEW_YORK).date()
    duration_ms = _SPECS[timeframe]["duration_ms"]
    rows: dict[int, dict] = {}
    for timestamp, raw in data.iterrows():
        candle_time = _as_utc(timestamp)
        local_time = candle_time.astimezone(NEW_YORK)
        if timeframe == "5m":
            clock = local_time.time().replace(tzinfo=None)
            if not wall_time(9, 30) <= clock < wall_time(16, 0):
                continue
            if int(candle_time.timestamp() * 1000) + duration_ms > int(now_utc.timestamp() * 1000):
                continue
        elif local_time.date() >= today_ny:
            # Yahoo exposes today's forming D1 bar during the session.
            continue

        try:
            open_px = float(raw["Open"])
            high = float(raw["High"])
            low = float(raw["Low"])
            close = float(raw["Close"])
            volume = float(raw["Volume"])
        except (TypeError, ValueError) as exc:
            raise UsEquityDataError("source candle contains non-numeric OHLCV") from exc
        values = (open_px, high, low, close, volume)
        if not all(isfinite(value) for value in values):
            raise UsEquityDataError("source candle contains non-finite OHLCV")
        if close <= 0 or volume < 0 or high < max(open_px, close) or low > min(open_px, close) or low > high:
            raise UsEquityDataError("source candle violates OHLCV bounds")
        ts = int(candle_time.timestamp() * 1000)
        rows[ts] = {
            "ts": ts,
            "open": open_px,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    candles = [rows[key] for key in sorted(rows)]
    if not candles:
        raise UsEquityDataError("no complete regular-session candles")
    return candles


def fetch_us_equity_candles(
    symbol: str,
    timeframe: str,
    limit: int,
    *,
    now: datetime | None = None,
    cache: bool = True,
) -> list[dict]:
    if symbol.upper() != "NVDA":
        raise UsEquityDataError(f"unsupported US-equity symbol {symbol!r}")
    if timeframe not in _SPECS:
        raise UsEquityDataError(f"unsupported US-equity timeframe {timeframe!r}")
    if limit <= 0:
        return []
    key = (symbol.upper(), timeframe)
    monotonic_now = time.monotonic()
    if cache:
        with _cache_lock:
            cached = _cache.get(key)
            if cached and monotonic_now - cached[0] < _CACHE_TTL_SECONDS:
                return [dict(row) for row in cached[1][-limit:]]
    spec = _SPECS[timeframe]
    frame = _download(symbol.upper(), period=spec["period"], interval=spec["interval"])
    candles = _normalize(frame, timeframe=timeframe, now=now or datetime.now(timezone.utc))
    with _cache_lock:
        _cache[key] = (monotonic_now, candles)
    return [dict(row) for row in candles[-limit:]]
