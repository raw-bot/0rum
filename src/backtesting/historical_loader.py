"""Offline historical candle loader for Phase 5 bootstrap datasets.

HistData is used here only as a historical bootstrap source for the
walk-forward optimizer. It is not a runtime market-data provider.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.database import AsyncSessionLocal
from src.models.candle import Candle

log = structlog.get_logger(__name__)

INSTRUMENT = "XAUUSD"
SOURCE_TIMEZONE = timezone(timedelta(hours=-5), name="EST")
SUPPORTED_TIMEFRAMES = ("M15", "H1", "H4", "D1")
TIMEFRAME_RULES = {
    "M1": "1min",
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}
POSTGRES_MAX_BIND_PARAMS = 32767


@dataclass(frozen=True)
class HistDataM1Bar:
    """One HistData Generic ASCII M1 bid bar normalized to UTC."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class HistoricalCandleRecord:
    """Candle row ready for insertion into the existing candles table."""

    instrument: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    complete: bool = True


@dataclass(frozen=True)
class ArchiveQA:
    """Read-only QA summary for one HistData archive."""

    archive: Path
    csv_name: str
    rows: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    status_name: str | None


def parse_histdata_timestamp(raw_value: str) -> datetime:
    """Parse HistData's fixed EST timestamp and convert it to UTC.

    HistData documents Generic ASCII timestamps as EST without daylight-saving
    adjustments, so a fixed UTC-05:00 offset is intentional here.
    """
    local_dt = datetime.strptime(raw_value.strip(), "%Y%m%d %H%M%S")
    return local_dt.replace(tzinfo=SOURCE_TIMEZONE).astimezone(timezone.utc)


def parse_histdata_m1_line(line: str) -> HistDataM1Bar:
    """Parse one Generic ASCII M1 line.

    Expected row format:
      YYYYMMDD HHMMSS;open_bid;high_bid;low_bid;close_bid;volume
    """
    parts = line.strip().split(";")
    if len(parts) != 6:
        raise ValueError(f"Expected 6 fields, got {len(parts)}.")

    ts_raw, open_raw, high_raw, low_raw, close_raw, volume_raw = parts
    return HistDataM1Bar(
        timestamp=parse_histdata_timestamp(ts_raw),
        open=Decimal(open_raw),
        high=Decimal(high_raw),
        low=Decimal(low_raw),
        close=Decimal(close_raw),
        volume=int(volume_raw),
    )


def _csv_member_name(zip_file: ZipFile) -> str:
    csv_names = [name for name in zip_file.namelist() if name.lower().endswith(".csv")]
    if len(csv_names) != 1:
        raise ValueError(f"Expected exactly one CSV member, found {csv_names}.")
    return csv_names[0]


def iter_histdata_m1_archive(archive_path: Path) -> Iterable[HistDataM1Bar]:
    """Yield parsed M1 bars from a HistData ZIP archive."""
    with ZipFile(archive_path) as zf:
        csv_name = _csv_member_name(zf)
        with zf.open(csv_name) as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.decode("ascii", errors="strict").strip()
                if not line:
                    continue
                try:
                    yield parse_histdata_m1_line(line)
                except Exception as exc:
                    raise ValueError(
                        f"Failed parsing {archive_path}:{csv_name}:{line_no}: {exc}"
                    ) from exc


def qa_histdata_archive(archive_path: Path) -> ArchiveQA:
    """Return read-only row count and timestamp range for one archive."""
    rows = 0
    first: datetime | None = None
    last: datetime | None = None
    with ZipFile(archive_path) as zf:
        csv_name = _csv_member_name(zf)
        status_names = [name for name in zf.namelist() if name.lower().endswith(".txt")]
        for bar in iter_histdata_m1_archive(archive_path):
            rows += 1
            if first is None:
                first = bar.timestamp
            last = bar.timestamp
    return ArchiveQA(
        archive=archive_path,
        csv_name=csv_name,
        rows=rows,
        first_timestamp=first,
        last_timestamp=last,
        status_name=status_names[0] if status_names else None,
    )


def load_m1_dataframe(archive_paths: Sequence[Path]) -> pd.DataFrame:
    """Load one or more HistData archives into a UTC-indexed M1 DataFrame."""
    rows: list[dict[str, Any]] = []
    for archive_path in archive_paths:
        for bar in iter_histdata_m1_archive(archive_path):
            rows.append(
                {
                    "timestamp": bar.timestamp,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": bar.volume,
                }
            )

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame.from_records(rows)
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.sort_values("timestamp").set_index("timestamp")
    return df


def resample_m1_dataframe(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample M1 bid bars to a supported optimizer timeframe."""
    if timeframe not in TIMEFRAME_RULES:
        raise ValueError(f"Unsupported timeframe '{timeframe}'.")
    if df.empty:
        return df.copy()

    rule = TIMEFRAME_RULES[timeframe]
    resample_kwargs = {"label": "left", "closed": "left"}
    if timeframe != "D1":
        resample_kwargs["origin"] = "epoch"

    resampled = df.resample(rule, **resample_kwargs).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return resampled.dropna(subset=["open", "high", "low", "close"])


def dataframe_to_candle_records(
    df: pd.DataFrame,
    *,
    timeframe: str,
    instrument: str = INSTRUMENT,
) -> list[HistoricalCandleRecord]:
    """Convert a resampled DataFrame into insertable candle records."""
    records: list[HistoricalCandleRecord] = []
    for timestamp, row in df.iterrows():
        ts = timestamp.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        records.append(
            HistoricalCandleRecord(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=ts,
                open=Decimal(str(round(float(row["open"]), 5))),
                high=Decimal(str(round(float(row["high"]), 5))),
                low=Decimal(str(round(float(row["low"]), 5))),
                close=Decimal(str(round(float(row["close"]), 5))),
                volume=int(row.get("volume", 0)),
                complete=True,
            )
        )
    return records


def build_candle_records_by_timeframe(
    archive_paths: Sequence[Path],
    *,
    timeframes: Sequence[str] = SUPPORTED_TIMEFRAMES,
) -> dict[str, list[HistoricalCandleRecord]]:
    """Load HistData M1 archives and build all requested timeframe records."""
    m1_df = load_m1_dataframe(archive_paths)
    return {
        timeframe: dataframe_to_candle_records(
            resample_m1_dataframe(m1_df, timeframe),
            timeframe=timeframe,
        )
        for timeframe in timeframes
    }


def _row_dict(record: HistoricalCandleRecord) -> dict[str, Any]:
    return {
        "instrument": record.instrument,
        "timeframe": record.timeframe,
        "timestamp": record.timestamp,
        "open": record.open,
        "high": record.high,
        "low": record.low,
        "close": record.close,
        "volume": record.volume,
        "complete": record.complete,
    }


def _effective_batch_size(
    *,
    dialect_name: str,
    requested_batch_size: int,
    column_count: int,
) -> int:
    if requested_batch_size <= 0:
        raise ValueError("batch_size must be greater than 0.")

    if dialect_name != "postgresql":
        return requested_batch_size

    max_rows = max(1, POSTGRES_MAX_BIND_PARAMS // column_count)
    return min(requested_batch_size, max_rows)


def _insert_statement_for_dialect(dialect_name: str, rows: list[dict[str, Any]]):
    if dialect_name == "sqlite":
        insert_stmt = sqlite_insert(Candle)
    else:
        insert_stmt = pg_insert(Candle)
    return insert_stmt.values(rows).on_conflict_do_nothing(
        index_elements=["instrument", "timeframe", "timestamp"]
    )


async def bulk_insert_candles(
    records: Sequence[HistoricalCandleRecord],
    *,
    session_factory: async_sessionmaker = AsyncSessionLocal,
    batch_size: int = 5000,
) -> int:
    """Insert candle records idempotently into the candles table."""
    if not records:
        return 0

    total_inserted = 0
    async with session_factory() as session:
        bind = session.get_bind()
        dialect_name = bind.dialect.name
        column_count = len(_row_dict(records[0]))
        effective_batch_size = _effective_batch_size(
            dialect_name=dialect_name,
            requested_batch_size=batch_size,
            column_count=column_count,
        )
        for start in range(0, len(records), effective_batch_size):
            batch = records[start:start + effective_batch_size]
            stmt = _insert_statement_for_dialect(
                dialect_name,
                [_row_dict(record) for record in batch],
            )
            result = await session.execute(stmt)
            total_inserted += result.rowcount or 0
        await session.commit()

    log.info("historical_loader.candles_inserted", count=total_inserted)
    return total_inserted


async def import_histdata_archives(
    archive_paths: Sequence[Path],
    *,
    session_factory: async_sessionmaker = AsyncSessionLocal,
    timeframes: Sequence[str] = SUPPORTED_TIMEFRAMES,
    batch_size: int = 5000,
) -> dict[str, int]:
    """Build and insert optimizer timeframes from HistData M1 archives."""
    records_by_tf = build_candle_records_by_timeframe(
        archive_paths,
        timeframes=timeframes,
    )
    inserted: dict[str, int] = {}
    for timeframe, records in records_by_tf.items():
        inserted[timeframe] = await bulk_insert_candles(
            records,
            session_factory=session_factory,
            batch_size=batch_size,
        )
        log.info(
            "historical_loader.timeframe_imported",
            timeframe=timeframe,
            built=len(records),
            inserted=inserted[timeframe],
        )
    return inserted
