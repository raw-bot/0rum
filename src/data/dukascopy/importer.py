"""Import cached Dukascopy OHLCV research exports into candles."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.backtesting.historical_loader import (
    SUPPORTED_TIMEFRAMES,
    bulk_insert_candles,
)
from src.backtesting.research_loader import dataframe_to_research_records
from src.data.dukascopy.qa import load_cached_ohlcv
from src.database import AsyncSessionLocal


@dataclass(frozen=True)
class DukascopyImportReport:
    timeframe: str
    source_rows: int
    built_rows: int
    inserted_rows: int
    dry_run: bool


def _as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC and normalize aware datetimes to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_timeframe(timeframe: str) -> None:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported Dukascopy import timeframe: {timeframe}")


def _indexed_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    indexed = df.copy()
    indexed["timestamp"] = pd.to_datetime(indexed["timestamp"], utc=True)
    return indexed.sort_values("timestamp").set_index("timestamp")


async def import_dukascopy_ohlcv_cache(
    *,
    cache_dir: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframes: Sequence[str] = SUPPORTED_TIMEFRAMES,
    instrument: str = "XAUUSD",
    dry_run: bool = False,
    batch_size: int = 5000,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> dict[str, DukascopyImportReport]:
    """Import cached Dukascopy OHLCV CSVs into the research candles table."""
    for timeframe in timeframes:
        _validate_timeframe(timeframe)

    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    reports: dict[str, DukascopyImportReport] = {}

    for timeframe in timeframes:
        df = load_cached_ohlcv(
            Path(cache_dir),
            symbol,
            start_utc,
            end_utc,
            timeframe,
        )
        records = dataframe_to_research_records(
            _indexed_ohlcv(df),
            instrument=instrument,
            timeframe=timeframe,
        )
        inserted_rows = 0
        if not dry_run:
            inserted_rows = await bulk_insert_candles(
                records,
                session_factory=session_factory,
                batch_size=batch_size,
            )
        reports[timeframe] = DukascopyImportReport(
            timeframe=timeframe,
            source_rows=len(df),
            built_rows=len(records),
            inserted_rows=inserted_rows,
            dry_run=dry_run,
        )

    return reports
