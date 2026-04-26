"""Unit tests for GapDetector — uses in-memory SQLite for candle data."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.ingestion.gap_detector import GapDetector


# ── In-memory SQLite fixture ────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# SQLite-compatible DDL for the candles table.
# The ORM model uses BigInteger (autoincrement), server_default="NOW()", and
# PostgreSQL-specific UniqueConstraint naming — none of which map cleanly to
# SQLite. We define a minimal SQLite schema matching the columns queried by
# GapDetector.find_gaps(): instrument, timeframe, timestamp.
_CREATE_CANDLES = """
CREATE TABLE candles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument  VARCHAR(10) NOT NULL DEFAULT 'XAUUSD',
    timeframe   VARCHAR(5)  NOT NULL,
    timestamp   DATETIME    NOT NULL,
    open        REAL        NOT NULL,
    high        REAL        NOT NULL,
    low         REAL        NOT NULL,
    close       REAL        NOT NULL,
    volume      INTEGER     NOT NULL,
    complete    INTEGER     NOT NULL DEFAULT 1,
    created_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_DROP_CANDLES = "DROP TABLE IF EXISTS candles"


@pytest_asyncio.fixture
async def async_session():
    """Provide an AsyncSession backed by in-memory SQLite with the candles table.

    Uses raw DDL instead of ORM metadata.create_all() to avoid PostgreSQL-only
    types (JSONB, gen_random_uuid()) that are incompatible with SQLite.
    """
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(_CREATE_CANDLES))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.execute(text(_DROP_CANDLES))
    await engine.dispose()


async def _seed_candles(session: AsyncSession, timeframe: str, timestamps: list[datetime]) -> None:
    """Insert candle rows via raw SQL — bypasses ORM BigInteger autoincrement issue."""
    for ts in timestamps:
        await session.execute(
            text(
                "INSERT INTO candles "
                "(instrument, timeframe, timestamp, open, high, low, close, volume, complete) "
                "VALUES (:instrument, :timeframe, :timestamp, :open, :high, :low, :close, :volume, :complete)"
            ),
            {
                "instrument": "XAUUSD",
                "timeframe": timeframe,
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": 2000.0,
                "high": 2010.0,
                "low": 1995.0,
                "close": 2005.0,
                "volume": 100,
                "complete": 1,
            },
        )
    await session.commit()


def _recent_base() -> datetime:
    """Return a base timestamp within the last 24h so it falls inside lookback_hours=48."""
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=24)


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_gaps_no_gaps_returns_empty(async_session: AsyncSession):
    """No gaps when candles are consecutive at 15-minute intervals."""
    base = _recent_base()
    timestamps = [base + timedelta(minutes=15 * i) for i in range(10)]
    await _seed_candles(async_session, "M15", timestamps)

    detector = GapDetector.__new__(GapDetector)
    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gaps = await detector.find_gaps("XAUUSD", "M15", lookback_hours=48)

    assert gaps == []


@pytest.mark.asyncio
async def test_find_gaps_detects_missing_candle(async_session: AsyncSession):
    """Two missing M15 candles (indices 4 and 5) create a gap > 2× interval."""
    base = _recent_base()
    # Skip indices 4 and 5: delta between index 3 and 6 = 3×15 = 45min > 2×15=30min
    # find_gaps uses `delta > expected_delta * 2` — one missing candle is not enough.
    indices = [0, 1, 2, 3, 6, 7]
    timestamps = [base + timedelta(minutes=15 * i) for i in indices]
    await _seed_candles(async_session, "M15", timestamps)

    detector = GapDetector.__new__(GapDetector)
    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gaps = await detector.find_gaps("XAUUSD", "M15", lookback_hours=48)

    assert len(gaps) == 1
    assert gaps[0].timeframe == "M15"


@pytest.mark.asyncio
async def test_find_gaps_does_not_mask_friday_gap_before_market_close(
    async_session: AsyncSession,
):
    """A Friday gap starting before 20:00 UTC is a data gap, not a weekend close."""
    timestamps = [
        datetime(2026, 4, 24, 19, 59, tzinfo=timezone.utc),  # Friday
        datetime(2026, 4, 26, 19, 59, tzinfo=timezone.utc),  # Sunday
    ]
    await _seed_candles(async_session, "M15", timestamps)

    detector = GapDetector.__new__(GapDetector)
    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gaps = await detector.find_gaps("XAUUSD", "M15", lookback_hours=24 * 365)

    assert len(gaps) == 1


@pytest.mark.asyncio
async def test_find_gaps_masks_expected_weekend_market_close(async_session: AsyncSession):
    """Friday 20:00 UTC to Sunday 20:00 UTC is treated as expected closure."""
    timestamps = [
        datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),  # Friday
        datetime(2026, 4, 26, 20, 0, tzinfo=timezone.utc),  # Sunday
    ]
    await _seed_candles(async_session, "M15", timestamps)

    detector = GapDetector.__new__(GapDetector)
    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gaps = await detector.find_gaps("XAUUSD", "M15", lookback_hours=24 * 365)

    assert gaps == []


@pytest.mark.asyncio
async def test_find_gaps_does_not_mask_sunday_gap_before_market_reopen(
    async_session: AsyncSession,
):
    """A Sunday candle before 20:00 UTC should not satisfy the weekend-open mask."""
    timestamps = [
        datetime(2026, 4, 24, 20, 0, tzinfo=timezone.utc),  # Friday
        datetime(2026, 4, 26, 19, 59, tzinfo=timezone.utc),  # Sunday
    ]
    await _seed_candles(async_session, "M15", timestamps)

    detector = GapDetector.__new__(GapDetector)
    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gaps = await detector.find_gaps("XAUUSD", "M15", lookback_hours=24 * 365)

    assert len(gaps) == 1


@pytest.mark.asyncio
async def test_detect_and_fill_calls_fetch_per_gap(async_session: AsyncSession):
    """detect_and_fill() calls fetch_and_store once per detected gap."""
    base = _recent_base()
    # Skip indices 4 and 5 to create a gap > 2× interval (same logic as above)
    indices = [0, 1, 2, 3, 6, 7]
    timestamps = [base + timedelta(minutes=15 * i) for i in indices]
    await _seed_candles(async_session, "M15", timestamps)

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_and_store = AsyncMock(return_value=1)

    detector = GapDetector(fetcher=mock_fetcher)
    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gap_count = await detector.detect_and_fill("XAUUSD", "M15", lookback_hours=48)

    assert gap_count == 1
    mock_fetcher.fetch_and_store.assert_called_once()


@pytest.mark.asyncio
async def test_find_gaps_empty_table_returns_empty(async_session: AsyncSession):
    """find_gaps returns empty list when the candles table has no rows."""
    detector = GapDetector.__new__(GapDetector)
    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gaps = await detector.find_gaps("XAUUSD", "H1", lookback_hours=48)

    assert gaps == []


@pytest.mark.asyncio
async def test_detect_and_fill_skips_oversized_gap(async_session: AsyncSession):
    """detect_and_fill skips gaps larger than max_gap_bars without fetching."""
    base = _recent_base()
    # Gap of 6 bars (delta=90 min > 2×15=30 min threshold), so find_gaps detects it.
    timestamps = [base, base + timedelta(minutes=90)]
    await _seed_candles(async_session, "M15", timestamps)

    mock_fetcher = AsyncMock()
    detector = GapDetector(fetcher=mock_fetcher, max_gap_bars=3)

    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gap_count = await detector.detect_and_fill("XAUUSD", "M15", lookback_hours=48)

    assert gap_count == 1
    mock_fetcher.fetch_and_store.assert_not_called()


@pytest.mark.asyncio
async def test_detect_and_fill_passes_to_time_on_fill(async_session: AsyncSession):
    """detect_and_fill always passes to_time to bound the historical request."""
    base = _recent_base()
    timestamps = [base, base + timedelta(minutes=90)]
    await _seed_candles(async_session, "M15", timestamps)

    mock_fetcher = AsyncMock()
    mock_fetcher.fetch_and_store = AsyncMock(return_value=1)
    detector = GapDetector(fetcher=mock_fetcher, max_gap_bars=10)

    with pytest.MonkeyPatch().context() as mp:
        import src.ingestion.gap_detector as gd_module
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_session_ctx():
            yield async_session

        mp.setattr(gd_module, "AsyncSessionLocal", mock_session_ctx)
        gap_count = await detector.detect_and_fill("XAUUSD", "M15", lookback_hours=48)

    assert gap_count == 1
    mock_fetcher.fetch_and_store.assert_called_once()
    kwargs = mock_fetcher.fetch_and_store.call_args.kwargs
    assert kwargs.get("to_time") is not None
    assert kwargs.get("from_time") is not None
