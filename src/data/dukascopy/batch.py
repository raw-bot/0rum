"""Guarded batch planning for research-only Dukascopy downloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.data.dukascopy.bi5 import iter_utc_hours, normalize_symbol, ohlcv_cache_path, raw_bi5_cache_path


DEFAULT_SAFE_DAYS_WITHOUT_MAX = 31
DEFAULT_TIMEFRAMES = ("M15", "H1", "H4", "D1")


@dataclass(frozen=True)
class DukascopyBatch:
    """One half-open UTC batch window with cache accounting."""

    symbol: str
    start: datetime
    end: datetime
    expected_hours: int
    cached_raw_files: int
    missing_raw_files: int
    ohlcv_paths: dict[str, Path]
    complete_ohlcv_timeframes: tuple[str, ...]


@dataclass(frozen=True)
class DukascopyBatchPlan:
    """Cautious plan for a bounded Dukascopy download range."""

    symbol: str
    start: datetime
    end: datetime
    batch_days: int
    max_days: int | None
    total_days: float
    total_expected_hours: int
    total_cached_raw_files: int
    total_missing_raw_files: int
    dry_run: bool
    batches: tuple[DukascopyBatch, ...]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_range(start: datetime, end: datetime, *, max_days: int | None) -> float:
    if end <= start:
        raise ValueError("end must be after start")

    total_days = (end - start).total_seconds() / 86400
    if max_days is None and total_days > DEFAULT_SAFE_DAYS_WITHOUT_MAX:
        raise RuntimeError(f"Refusing to plan {total_days:.2f} days without --max-days")
    if max_days is not None and total_days > max_days:
        raise RuntimeError(f"Refusing to plan {total_days:.2f} days; --max-days is {max_days}")
    return total_days


def _build_one_batch(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    timeframes: tuple[str, ...],
) -> DukascopyBatch:
    hours = iter_utc_hours(start, end)
    cached_raw_files = sum(1 for hour in hours if raw_bi5_cache_path(cache_dir, symbol, hour).exists())
    ohlcv_paths = {
        timeframe: ohlcv_cache_path(cache_dir, symbol, start, end, timeframe) for timeframe in timeframes
    }
    complete_ohlcv_timeframes = tuple(timeframe for timeframe, path in ohlcv_paths.items() if path.exists())

    return DukascopyBatch(
        symbol=symbol,
        start=start,
        end=end,
        expected_hours=len(hours),
        cached_raw_files=cached_raw_files,
        missing_raw_files=len(hours) - cached_raw_files,
        ohlcv_paths=ohlcv_paths,
        complete_ohlcv_timeframes=complete_ohlcv_timeframes,
    )


def build_batch_plan(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    batch_days: int,
    cache_dir: Path,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    max_days: int | None = None,
    dry_run: bool = True,
) -> DukascopyBatchPlan:
    """Build a guarded half-open UTC batch plan for Dukascopy research data."""
    if batch_days <= 0:
        raise ValueError("batch_days must be greater than 0")
    if not timeframes:
        raise ValueError("timeframes must not be empty")

    symbol_norm = normalize_symbol(symbol)
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    total_days = _validate_range(start_utc, end_utc, max_days=max_days)

    batch_delta = timedelta(days=batch_days)
    batches: list[DukascopyBatch] = []
    cursor = start_utc
    while cursor < end_utc:
        batch_end = min(cursor + batch_delta, end_utc)
        batches.append(
            _build_one_batch(
                symbol=symbol_norm,
                start=cursor,
                end=batch_end,
                cache_dir=cache_dir,
                timeframes=timeframes,
            )
        )
        cursor = batch_end

    return DukascopyBatchPlan(
        symbol=symbol_norm,
        start=start_utc,
        end=end_utc,
        batch_days=batch_days,
        max_days=max_days,
        total_days=total_days,
        total_expected_hours=sum(batch.expected_hours for batch in batches),
        total_cached_raw_files=sum(batch.cached_raw_files for batch in batches),
        total_missing_raw_files=sum(batch.missing_raw_files for batch in batches),
        dry_run=dry_run,
        batches=tuple(batches),
    )
