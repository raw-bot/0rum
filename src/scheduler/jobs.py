"""APScheduler job definitions for candle refresh across 4 timeframes."""

import asyncio

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config import get_settings
from src.ingestion.candle_fetcher import CandleFetcher
from src.ingestion.gap_detector import GapDetector

log = structlog.get_logger(__name__)

# Module-level state: timestamp of last successful candle fetch per timeframe.
# GIL-protected dict assignment — safe for concurrent async tasks.
_last_candle_fetch: dict[str, str | None] = {
    "M15": None,
    "H1": None,
    "H4": None,
    "D1": None,
}


def get_last_candle_fetch() -> dict[str, str | None]:
    """Return a copy of the last fetch timestamps for health endpoint wiring."""
    return dict(_last_candle_fetch)


async def _refresh_timeframe(timeframe: str) -> None:
    """Fetch latest candles for one timeframe, run gap detection, update last_fetch."""
    fetcher = CandleFetcher()
    detector = GapDetector(fetcher=fetcher)
    try:
        await fetcher.fetch_and_store(
            instrument="XAUUSD",
            timeframe=timeframe,
            count=10,  # only recent candles needed for scheduled refresh
        )
        from datetime import datetime, timezone
        _last_candle_fetch[timeframe] = datetime.now(timezone.utc).isoformat()

        # Run gap detection after each fetch
        gaps_found = await detector.detect_and_fill(
            instrument="XAUUSD",
            timeframe=timeframe,
            lookback_hours=48,
        )
        if gaps_found:
            log.info("jobs.gap_check_complete", timeframe=timeframe, gaps_found=gaps_found)

        # Prune stale incomplete candles
        await fetcher.prune_incomplete(instrument="XAUUSD")

    except Exception as exc:
        log.error("jobs.refresh_failed", timeframe=timeframe, error=str(exc))


async def refresh_m15() -> None:
    """Refresh M15 candles — called every 15 minutes."""
    await _refresh_timeframe("M15")


async def refresh_h1() -> None:
    """Refresh H1 candles — called every hour."""
    await _refresh_timeframe("H1")


async def refresh_h4() -> None:
    """Refresh H4 candles — called every 4 hours."""
    await _refresh_timeframe("H4")


async def refresh_d1() -> None:
    """Refresh D1 candles — called daily at 00:05 UTC."""
    await _refresh_timeframe("D1")


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the APScheduler instance with all 4 candle jobs.

    Cadences (CLAUDE.md section 8.2):
      M15 → every 15 minutes
      H1  → every 1 hour
      H4  → every 4 hours
      D1  → daily at 00:05 UTC

    All jobs use max_instances=1 to prevent job pile-up on slow fetches.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        refresh_m15,
        trigger=IntervalTrigger(minutes=15),
        id="refresh_m15",
        name="Refresh M15 candles",
        max_instances=1,
        replace_existing=True,
    )

    scheduler.add_job(
        refresh_h1,
        trigger=IntervalTrigger(hours=1),
        id="refresh_h1",
        name="Refresh H1 candles",
        max_instances=1,
        replace_existing=True,
    )

    scheduler.add_job(
        refresh_h4,
        trigger=IntervalTrigger(hours=4),
        id="refresh_h4",
        name="Refresh H4 candles",
        max_instances=1,
        replace_existing=True,
    )

    scheduler.add_job(
        refresh_d1,
        trigger=CronTrigger(hour=0, minute=5, timezone="UTC"),
        id="refresh_d1",
        name="Refresh D1 candles daily at 00:05 UTC",
        max_instances=1,
        replace_existing=True,
    )

    return scheduler
