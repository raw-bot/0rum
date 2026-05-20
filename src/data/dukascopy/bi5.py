"""Dukascopy public `.bi5` tick downloader and parser.

This module is research-only. It downloads public historical tick files,
normalizes them to UTC timestamps, and can aggregate them into OHLCV candles for
local cache/backtest use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import lzma
from pathlib import Path
import struct
from typing import Callable

import httpx
import pandas as pd


DUKASCOPY_DATAFEED_URL = "https://datafeed.dukascopy.com/datafeed"
TICK_STRUCT = struct.Struct(">IIIff")
DEFAULT_PRICE_SCALES: dict[str, int] = {
    "XAUUSD": 1000,
}
RESAMPLE_RULES: dict[str, str] = {
    "M1": "1min",
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}


@dataclass(frozen=True)
class DukascopyDownloadSummary:
    """Summary for one cautious Dukascopy download run."""

    symbol: str
    start: datetime
    end: datetime
    raw_files: int
    tick_rows: int
    cache_dir: Path
    ohlcv_paths: dict[str, Path]


def normalize_symbol(symbol: str) -> str:
    """Normalize symbols to Dukascopy datafeed path style."""
    return symbol.upper().replace("/", "").replace(":", "")


def _as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and normalize aware datetimes to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def price_scale_for_symbol(symbol: str, price_scale: int | None = None) -> int:
    """Return integer price divisor used by Dukascopy tick prices."""
    if price_scale is not None:
        return price_scale
    return DEFAULT_PRICE_SCALES.get(normalize_symbol(symbol), 100000)


def build_dukascopy_bi5_url(
    symbol: str,
    hour_start: datetime,
    *,
    base_url: str = DUKASCOPY_DATAFEED_URL,
) -> str:
    """Build the public Dukascopy URL for one hourly tick file.

    Dukascopy datafeed paths use zero-based months: January is `00`.
    """
    utc_hour = _as_utc(hour_start)
    month_zero_based = utc_hour.month - 1
    return (
        f"{base_url.rstrip('/')}/{normalize_symbol(symbol)}/"
        f"{utc_hour.year:04d}/{month_zero_based:02d}/{utc_hour.day:02d}/"
        f"{utc_hour.hour:02d}h_ticks.bi5"
    )


def iter_utc_hours(start: datetime, end: datetime) -> list[datetime]:
    """Return UTC hour starts for `[start, end)`."""
    start_utc = _as_utc(start).replace(minute=0, second=0, microsecond=0)
    end_utc = _as_utc(end)
    hours: list[datetime] = []
    cursor = start_utc
    while cursor < end_utc:
        hours.append(cursor)
        cursor += timedelta(hours=1)
    return hours


def decompress_bi5(raw: bytes) -> bytes:
    """Decompress one Dukascopy LZMA `.bi5` payload."""
    if not raw:
        return b""
    return lzma.decompress(raw)


def parse_tick_bi5(
    payload: bytes,
    *,
    hour_start: datetime,
    symbol: str = "XAUUSD",
    price_scale: int | None = None,
) -> pd.DataFrame:
    """Parse decompressed Dukascopy tick bytes into a DataFrame.

    The observed public tick layout is big-endian `uint32,uint32,uint32,float,float`:
    millisecond offset inside the hour, ask integer, bid integer, ask volume,
    bid volume. Prices are divided by the instrument scale.
    """
    if len(payload) % TICK_STRUCT.size != 0:
        raise ValueError(
            f"Invalid Dukascopy tick payload size: {len(payload)} bytes "
            f"is not divisible by {TICK_STRUCT.size}"
        )

    scale = price_scale_for_symbol(symbol, price_scale)
    hour_utc = _as_utc(hour_start).replace(minute=0, second=0, microsecond=0)
    rows: list[dict[str, object]] = []
    for offset in range(0, len(payload), TICK_STRUCT.size):
        ms_offset, ask_raw, bid_raw, ask_volume, bid_volume = TICK_STRUCT.unpack_from(payload, offset)
        rows.append(
            {
                "timestamp": hour_utc + timedelta(milliseconds=int(ms_offset)),
                "bid": bid_raw / scale,
                "ask": ask_raw / scale,
                "bid_volume": float(bid_volume),
                "ask_volume": float(ask_volume),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=["bid", "ask", "bid_volume", "ask_volume"],
            index=pd.DatetimeIndex([], name="timestamp", tz=UTC),
        )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").set_index("timestamp")


def resample_ticks_to_ohlcv(ticks: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate bid/ask ticks into mid-price OHLCV candles."""
    if timeframe not in RESAMPLE_RULES:
        raise ValueError(f"Unsupported Dukascopy resample timeframe: {timeframe}")
    if ticks.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    working = ticks.copy()
    working["mid"] = (working["bid"].astype(float) + working["ask"].astype(float)) / 2.0
    working["volume"] = working["bid_volume"].fillna(0.0) + working["ask_volume"].fillna(0.0)
    candles = working.resample(RESAMPLE_RULES[timeframe], label="left", closed="left").agg(
        open=("mid", "first"),
        high=("mid", "max"),
        low=("mid", "min"),
        close=("mid", "last"),
        volume=("volume", "sum"),
    )
    return candles.dropna(subset=["open", "high", "low", "close"])


def _date_path(root: Path, symbol: str, hour_start: datetime) -> Path:
    hour_utc = _as_utc(hour_start)
    return (
        root
        / "raw"
        / normalize_symbol(symbol)
        / f"{hour_utc.year:04d}"
        / f"{hour_utc.month:02d}"
        / f"{hour_utc.day:02d}"
        / f"{hour_utc.hour:02d}h_ticks.bi5"
    )


def raw_bi5_cache_path(cache_dir: Path, symbol: str, hour_start: datetime) -> Path:
    """Return the local cache path for one raw hourly Dukascopy `.bi5` file."""
    return _date_path(cache_dir, symbol, hour_start)


def empty_bi5_cache_path(cache_dir: Path, symbol: str, hour_start: datetime) -> Path:
    """Return the local sidecar path marking a confirmed empty hourly file."""
    raw_path = raw_bi5_cache_path(cache_dir, symbol, hour_start)
    return raw_path.with_name(f"{raw_path.name}.empty")


def ticks_cache_path(cache_dir: Path, symbol: str, start: datetime, end: datetime) -> Path:
    """Return the local cache path for one exported tick CSV range."""
    symbol_norm = normalize_symbol(symbol)
    return (
        cache_dir
        / "ticks"
        / symbol_norm
        / f"{_as_utc(start):%Y%m%dT%H%M}_{_as_utc(end):%Y%m%dT%H%M}_ticks.csv"
    )


def ohlcv_cache_path(cache_dir: Path, symbol: str, start: datetime, end: datetime, timeframe: str) -> Path:
    """Return the local cache path for one exported OHLCV CSV range."""
    symbol_norm = normalize_symbol(symbol)
    return (
        cache_dir
        / "ohlcv"
        / symbol_norm
        / f"{_as_utc(start):%Y%m%dT%H%M}_{_as_utc(end):%Y%m%dT%H%M}_{timeframe}.csv"
    )


class DukascopyTickDownloader:
    """Cautious downloader for public Dukascopy hourly `.bi5` files."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        client: httpx.AsyncClient | None = None,
        base_url: str = DUKASCOPY_DATAFEED_URL,
        price_scale: int | None = None,
        retries: int = 2,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.cache_dir = cache_dir
        self.base_url = base_url
        self.price_scale = price_scale
        self.retries = retries
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the underlying HTTP client when owned by the downloader."""
        if self._owns_client:
            await self._client.aclose()

    async def fetch_hour(self, symbol: str, hour_start: datetime, *, force: bool = False) -> pd.DataFrame:
        """Fetch/cache/parse one hourly tick file."""
        raw_path = raw_bi5_cache_path(self.cache_dir, symbol, hour_start)
        empty_path = empty_bi5_cache_path(self.cache_dir, symbol, hour_start)
        if not force and not raw_path.exists() and empty_path.exists():
            return parse_tick_bi5(b"", hour_start=hour_start, symbol=symbol, price_scale=self.price_scale)

        if force or not raw_path.exists():
            url = build_dukascopy_bi5_url(symbol, hour_start, base_url=self.base_url)
            response = await self._get_with_retries(url)
            if response.status_code == 404:
                raw_path.unlink(missing_ok=True)
                empty_path.parent.mkdir(parents=True, exist_ok=True)
                empty_path.write_text("404\n", encoding="ascii")
                return parse_tick_bi5(b"", hour_start=hour_start, symbol=symbol, price_scale=self.price_scale)
            if response.status_code >= 400:
                raise RuntimeError(f"Dukascopy request failed: HTTP {response.status_code} for {url}")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(response.content)
            empty_path.unlink(missing_ok=True)

        payload = decompress_bi5(raw_path.read_bytes())
        return parse_tick_bi5(payload, hour_start=hour_start, symbol=symbol, price_scale=self.price_scale)

    async def _get_with_retries(self, url: str) -> httpx.Response:
        """GET one URL with bounded retries for slow public datafeed hours."""
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return await self._client.get(url)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt >= self.retries:
                    break
        raise RuntimeError(f"Dukascopy request timed out after {self.retries + 1} attempts: {url}") from last_exc

    async def download_range(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframes: tuple[str, ...] = ("M15", "H1", "H4", "D1"),
        force: bool = False,
        progress_callback: Callable[[int, int, datetime, int], None] | None = None,
    ) -> DukascopyDownloadSummary:
        """Download hourly files, write ticks and OHLCV CSVs, and return a summary."""
        symbol_norm = normalize_symbol(symbol)
        hours = iter_utc_hours(start, end)
        hourly_frames: list[pd.DataFrame] = []
        for index, hour_start in enumerate(hours, start=1):
            frame = await self.fetch_hour(symbol_norm, hour_start, force=force)
            hourly_frames.append(frame)
            if progress_callback is not None:
                progress_callback(index, len(hours), hour_start, len(frame))
        ticks = pd.concat(hourly_frames).sort_index() if hourly_frames else pd.DataFrame()
        start_utc = _as_utc(start)
        end_utc = _as_utc(end)
        ticks = ticks[(ticks.index >= start_utc) & (ticks.index < end_utc)]

        ticks_path = ticks_cache_path(self.cache_dir, symbol_norm, start, end)
        ticks_path.parent.mkdir(parents=True, exist_ok=True)
        ticks.to_csv(ticks_path, index_label="timestamp")

        ohlcv_paths: dict[str, Path] = {}
        for timeframe in timeframes:
            candles = resample_ticks_to_ohlcv(ticks, timeframe)
            output = ohlcv_cache_path(self.cache_dir, symbol_norm, start, end, timeframe)
            output.parent.mkdir(parents=True, exist_ok=True)
            candles.to_csv(output, index_label="timestamp")
            ohlcv_paths[timeframe] = output

        return DukascopyDownloadSummary(
            symbol=symbol_norm,
            start=start_utc,
            end=end_utc,
            raw_files=len(hours),
            tick_rows=len(ticks),
            cache_dir=self.cache_dir,
            ohlcv_paths=ohlcv_paths,
        )
