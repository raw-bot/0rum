"""Gap detector — scans candles table for temporal gaps and triggers backfill."""

from datetime import datetime, timezone
from typing import NamedTuple

import structlog
from sqlalchemy import select

from src.database import AsyncSessionLocal
from src.ingestion.candle_fetcher import CandleFetcher
from src.models.candle import Candle

log = structlog.get_logger(__name__)

# Expected gap sizes per timeframe (in minutes)
TIMEFRAME_MINUTES: dict[str, int] = {
    "M15": 15,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


class Gap(NamedTuple):
    timeframe: str
    gap_start: datetime
    gap_end: datetime


class GapDetector:
    """Detects temporal gaps in the candles table and triggers backfill."""

    def __init__(self, fetcher: CandleFetcher | None = None) -> None:
        self.fetcher = fetcher or CandleFetcher()

    async def find_gaps(
        self,
        instrument: str = "XAUUSD",
        timeframe: str = "M15",
        lookback_hours: int = 48,
    ) -> list[Gap]:
        """Scan the most recent lookback_hours of candles for temporal gaps.

        A gap exists when two consecutive candle timestamps differ by more than
        the expected timeframe interval (e.g., M15 → 15 minutes).

        Market closures (Sat 22:00 UTC to Sun 22:00 UTC) are excluded — a 24h
        weekend gap is expected and must not trigger backfill.

        Returns:
            List of Gap(timeframe, gap_start, gap_end) tuples.
        """
        from datetime import timedelta

        expected_delta = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Candle.timestamp)
                .where(
                    Candle.instrument == instrument,
                    Candle.timeframe == timeframe,
                    Candle.timestamp >= cutoff,
                )
                .order_by(Candle.timestamp.asc())
            )
            timestamps = [row[0] for row in result.fetchall()]

        if len(timestamps) < 2:
            return []

        gaps: list[Gap] = []
        for i in range(1, len(timestamps)):
            prev = timestamps[i - 1]
            curr = timestamps[i]
            delta = curr - prev

            # Allow up to 2× the expected interval before flagging
            if delta > expected_delta * 2:
                # Skip weekend gaps: Sat 22:00–Sun 22:00 UTC
                # Saturday = weekday 5, Sunday = weekday 6
                if prev.weekday() == 4 and delta.total_seconds() <= 172800:
                    # Friday close → Sunday open: up to 48h tolerated
                    continue
                gaps.append(Gap(timeframe=timeframe, gap_start=prev, gap_end=curr))

        return gaps

    async def detect_and_fill(
        self,
        instrument: str = "XAUUSD",
        timeframe: str = "M15",
        lookback_hours: int = 48,
    ) -> int:
        """Detect gaps and trigger a single fetch_and_store() for each gap.

        Does NOT retry if fetch returns 0 candles — logs as unresolved gap.
        Does NOT loop recursively — one pass per call.

        Returns:
            Number of gaps detected.
        """
        gaps = await self.find_gaps(
            instrument=instrument,
            timeframe=timeframe,
            lookback_hours=lookback_hours,
        )

        for gap in gaps:
            log.warning(
                "gap_detector.gap_detected",
                instrument=instrument,
                timeframe=timeframe,
                gap_start=gap.gap_start.isoformat(),
                gap_end=gap.gap_end.isoformat(),
            )
            try:
                from_time = gap.gap_start.strftime("%Y-%m-%dT%H:%M:%SZ")
                inserted = await self.fetcher.fetch_and_store(
                    instrument=instrument,
                    timeframe=timeframe,
                    from_time=from_time,
                )
                if inserted > 0:
                    log.info(
                        "gap_detector.gap_filled",
                        instrument=instrument,
                        timeframe=timeframe,
                        gap_start=gap.gap_start.isoformat(),
                        inserted=inserted,
                    )
                else:
                    log.warning(
                        "gap_detector.gap_unresolved",
                        instrument=instrument,
                        timeframe=timeframe,
                        gap_start=gap.gap_start.isoformat(),
                        gap_end=gap.gap_end.isoformat(),
                    )
            except Exception as exc:
                log.error(
                    "gap_detector.fill_error",
                    instrument=instrument,
                    timeframe=timeframe,
                    error=str(exc),
                )

        return len(gaps)
