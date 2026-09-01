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
_cache: dict[
    tuple[str, str, bool, str],
    tuple[float, list[dict], tuple[str, ...]],
] = {}


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


def _normalize(
    frame,
    *,
    timeframe: str,
    now: datetime,
    allow_trailing_daily_missing_close: bool = False,
    diagnostics: list[str] | None = None,
) -> list[dict]:
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
    candidates: list[tuple[datetime, tuple[float, float, float, float, float]]] = []
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
        candidates.append((candle_time, (open_px, high, low, close, volume)))

    timestamps = [int(candle_time.timestamp() * 1000) for candle_time, _values in candidates]
    if len(timestamps) != len(set(timestamps)):
        raise UsEquityDataError("source contains duplicate candle timestamp")

    invalid_indexes = [
        index
        for index, (_candle_time, values) in enumerate(candidates)
        if not all(isfinite(value) for value in values)
    ]
    skipped_index: int | None = None
    if invalid_indexes:
        if (
            allow_trailing_daily_missing_close
            and timeframe == "1d"
            and len(invalid_indexes) == 1
            and candidates
        ):
            invalid_index = invalid_indexes[0]
            candle_time, values = candidates[invalid_index]
            is_unique_latest = candle_time == max(row_time for row_time, _values in candidates)
            open_px, high, low, close, volume = values
            if (
                is_unique_latest
                and all(isfinite(value) for value in (open_px, high, low, volume))
                and not isfinite(close)
            ):
                skipped_index = invalid_index
                if diagnostics is not None:
                    diagnostics.append(
                        "bougie D1 invalide du "
                        f"{candle_time.astimezone(NEW_YORK).date().isoformat()} "
                        "écartée : clôture Yahoo non valide"
                    )
        if skipped_index is None:
            raise UsEquityDataError("source candle contains non-finite OHLCV")

    rows: dict[int, dict] = {}
    for index, (candle_time, values) in enumerate(candidates):
        if index == skipped_index:
            continue
        open_px, high, low, close, volume = values
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
    allow_trailing_daily_missing_close: bool = False,
    diagnostics: list[str] | None = None,
) -> list[dict]:
    if symbol.upper() != "NVDA":
        raise UsEquityDataError(f"unsupported US-equity symbol {symbol!r}")
    if timeframe not in _SPECS:
        raise UsEquityDataError(f"unsupported US-equity timeframe {timeframe!r}")
    if limit <= 0:
        return []
    observed_at = now or datetime.now(timezone.utc)
    key = (
        symbol.upper(),
        timeframe,
        allow_trailing_daily_missing_close,
        observed_at.astimezone(NEW_YORK).date().isoformat(),
    )
    monotonic_now = time.monotonic()
    if cache:
        with _cache_lock:
            cached = _cache.get(key)
            if cached and monotonic_now - cached[0] < _CACHE_TTL_SECONDS:
                if diagnostics is not None:
                    diagnostics.extend(cached[2])
                return [dict(row) for row in cached[1][-limit:]]
    spec = _SPECS[timeframe]
    frame = _download(symbol.upper(), period=spec["period"], interval=spec["interval"])
    batch_diagnostics: list[str] = []
    candles = _normalize(
        frame,
        timeframe=timeframe,
        now=observed_at,
        allow_trailing_daily_missing_close=allow_trailing_daily_missing_close,
        diagnostics=batch_diagnostics,
    )
    with _cache_lock:
        _cache[key] = (monotonic_now, candles, tuple(batch_diagnostics))
    if diagnostics is not None:
        diagnostics.extend(batch_diagnostics)
    return [dict(row) for row in candles[-limit:]]
