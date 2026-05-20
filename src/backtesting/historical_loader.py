"""Research candle insertion helpers shared by historical importers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.database import AsyncSessionLocal
from src.models.candle import Candle

log = structlog.get_logger(__name__)

INSTRUMENT = "XAUUSD"
SUPPORTED_TIMEFRAMES = ("M15", "H1", "H4", "D1")
POSTGRES_MAX_BIND_PARAMS = 32767


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


def dataframe_to_candle_records(
    df: pd.DataFrame,
    *,
    timeframe: str,
    instrument: str = INSTRUMENT,
) -> list[HistoricalCandleRecord]:
    """Convert an indexed OHLCV DataFrame into insertable candle records."""
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
