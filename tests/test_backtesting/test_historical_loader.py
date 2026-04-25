"""Unit tests for the Phase 5 offline HistData loader."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.backtesting.historical_loader import (
    HistoricalCandleRecord,
    _effective_batch_size,
    bulk_insert_candles,
    dataframe_to_candle_records,
    parse_histdata_m1_line,
    qa_histdata_archive,
    resample_m1_dataframe,
)
from src.models.candle import Candle


def test_parse_histdata_line_converts_fixed_est_to_utc():
    bar = parse_histdata_m1_line(
        "20240101 180000;2062.598000;2064.525000;2062.405000;2064.235000;0"
    )

    assert bar.timestamp == datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert bar.open == Decimal("2062.598000")
    assert bar.high == Decimal("2064.525000")
    assert bar.low == Decimal("2062.405000")
    assert bar.close == Decimal("2064.235000")
    assert bar.volume == 0


def test_parse_histdata_line_rejects_wrong_field_count():
    with pytest.raises(ValueError, match="Expected 6 fields"):
        parse_histdata_m1_line("20240101 180000;1;2;3")


def test_resample_m1_to_m15_ohlcv():
    index = pd.date_range("2024-01-01T00:00:00Z", periods=15, freq="min")
    df = pd.DataFrame(
        {
            "open": list(range(100, 115)),
            "high": list(range(101, 116)),
            "low": list(range(99, 114)),
            "close": list(range(100, 115)),
            "volume": [1] * 15,
        },
        index=index,
    )

    resampled = resample_m1_dataframe(df, "M15")

    assert len(resampled) == 1
    row = resampled.iloc[0]
    assert row["open"] == 100
    assert row["high"] == 115
    assert row["low"] == 99
    assert row["close"] == 114
    assert row["volume"] == 15


def test_dataframe_to_candle_records_rounds_to_model_precision():
    df = pd.DataFrame(
        {
            "open": [2000.123456],
            "high": [2001.123456],
            "low": [1999.123456],
            "close": [2000.654321],
            "volume": [12],
        },
        index=pd.DatetimeIndex([datetime(2024, 1, 1, tzinfo=timezone.utc)]),
    )

    records = dataframe_to_candle_records(df, timeframe="H1")

    assert records == [
        HistoricalCandleRecord(
            instrument="XAUUSD",
            timeframe="H1",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=Decimal("2000.12346"),
            high=Decimal("2001.12346"),
            low=Decimal("1999.12346"),
            close=Decimal("2000.65432"),
            volume=12,
            complete=True,
        )
    ]


def test_qa_histdata_archive_counts_rows(tmp_path: Path):
    archive = tmp_path / "sample.zip"
    with ZipFile(archive, "w") as zf:
        zf.writestr(
            "DAT_ASCII_XAUUSD_M1_202401.csv",
            "\n".join(
                [
                    "20240101 180000;1.0;2.0;0.5;1.5;0",
                    "20240101 180100;1.5;2.5;1.0;2.0;0",
                ]
            ),
        )
        zf.writestr("DAT_ASCII_XAUUSD_M1_202401.txt", "status")

    report = qa_histdata_archive(archive)

    assert report.rows == 2
    assert report.csv_name == "DAT_ASCII_XAUUSD_M1_202401.csv"
    assert report.status_name == "DAT_ASCII_XAUUSD_M1_202401.txt"
    assert report.first_timestamp == datetime(2024, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert report.last_timestamp == datetime(2024, 1, 1, 23, 1, tzinfo=timezone.utc)


def test_effective_batch_size_caps_postgres_parameter_count():
    assert (
        _effective_batch_size(
            dialect_name="postgresql",
            requested_batch_size=5000,
            column_count=9,
        )
        == 3640
    )
    assert (
        _effective_batch_size(
            dialect_name="sqlite",
            requested_batch_size=5000,
            column_count=9,
        )
        == 5000
    )


@pytest.mark.asyncio
async def test_bulk_insert_candles_is_idempotent_with_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument VARCHAR(10) NOT NULL,
                    timeframe VARCHAR(5) NOT NULL,
                    timestamp DATETIME NOT NULL,
                    open NUMERIC(12, 5) NOT NULL,
                    high NUMERIC(12, 5) NOT NULL,
                    low NUMERIC(12, 5) NOT NULL,
                    close NUMERIC(12, 5) NOT NULL,
                    volume INTEGER NOT NULL,
                    complete BOOLEAN NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (instrument, timeframe, timestamp)
                )
                """
            )
        )

    records = [
        HistoricalCandleRecord(
            instrument="XAUUSD",
            timeframe="H1",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=Decimal("2000.0"),
            high=Decimal("2001.0"),
            low=Decimal("1999.0"),
            close=Decimal("2000.5"),
            volume=1,
        )
    ]

    first_inserted = await bulk_insert_candles(records, session_factory=async_session)
    second_inserted = await bulk_insert_candles(records, session_factory=async_session)

    async with async_session() as session:
        result = await session.execute(select(Candle))
        rows = list(result.scalars().all())

    assert first_inserted == 1
    assert second_inserted == 0
    assert len(rows) == 1
