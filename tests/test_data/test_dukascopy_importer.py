"""Tests for importing cached Dukascopy OHLCV into research candles."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.data.dukascopy.bi5 import ohlcv_cache_path
from src.data.dukascopy.importer import import_dukascopy_ohlcv_cache
from src.models.candle import Candle


def _write_ohlcv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
                "2026-05-18T09:00:00Z,2000,2002,1999,2001,10",
                "2026-05-18T09:15:00Z,2001,2003,2000,2002,20",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


async def _sqlite_session_factory():
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
    return engine, async_session


@pytest.mark.asyncio
async def test_dry_run_does_not_insert_cached_ohlcv(tmp_path: Path):
    start = datetime(2026, 5, 18, 9, tzinfo=UTC)
    end = datetime(2026, 5, 18, 10, tzinfo=UTC)
    _write_ohlcv(ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15"))

    reports = await import_dukascopy_ohlcv_cache(
        cache_dir=tmp_path,
        symbol="XAUUSD",
        start=start,
        end=end,
        timeframes=("M15",),
        dry_run=True,
    )

    report = reports["M15"]
    assert report.source_rows == 2
    assert report.built_rows == 2
    assert report.inserted_rows == 0
    assert report.dry_run is True


@pytest.mark.asyncio
async def test_import_is_idempotent_with_sqlite(tmp_path: Path):
    start = datetime(2026, 5, 18, 9, tzinfo=UTC)
    end = datetime(2026, 5, 18, 10, tzinfo=UTC)
    _write_ohlcv(ohlcv_cache_path(tmp_path, "XAUUSD", start, end, "M15"))
    engine, async_session = await _sqlite_session_factory()

    try:
        first = await import_dukascopy_ohlcv_cache(
            cache_dir=tmp_path,
            symbol="XAUUSD",
            start=start,
            end=end,
            timeframes=("M15",),
            session_factory=async_session,
        )
        second = await import_dukascopy_ohlcv_cache(
            cache_dir=tmp_path,
            symbol="XAUUSD",
            start=start,
            end=end,
            timeframes=("M15",),
            session_factory=async_session,
        )

        async with async_session() as session:
            result = await session.execute(select(Candle))
            rows = list(result.scalars().all())

        assert first["M15"].inserted_rows == 2
        assert second["M15"].inserted_rows == 0
        assert len(rows) == 2
        assert {row.instrument for row in rows} == {"XAUUSD"}
        assert {row.timeframe for row in rows} == {"M15"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_rejects_unsupported_timeframe(tmp_path: Path):
    with pytest.raises(ValueError, match="Unsupported Dukascopy import timeframe: M5"):
        await import_dukascopy_ohlcv_cache(
            cache_dir=tmp_path,
            symbol="XAUUSD",
            start=datetime(2026, 5, 18, 9, tzinfo=UTC),
            end=datetime(2026, 5, 18, 10, tzinfo=UTC),
            timeframes=("M5",),
        )


@pytest.mark.asyncio
async def test_import_rejects_non_xauusd_symbol(tmp_path: Path):
    with pytest.raises(ValueError, match="Dukascopy import supports only XAUUSD"):
        await import_dukascopy_ohlcv_cache(
            cache_dir=tmp_path,
            symbol="XAGUSD",
            start=datetime(2026, 5, 18, 9, tzinfo=UTC),
            end=datetime(2026, 5, 18, 10, tzinfo=UTC),
            timeframes=("M15",),
        )
