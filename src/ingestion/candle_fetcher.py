"""Candle fetcher — fetch from FXCM via MetaAPI, store in PostgreSQL, backfill on startup."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config import Settings, get_settings
from src.database import AsyncSessionLocal
from src.ingestion.market_client import MarketDataClient
from src.models.candle import Candle

log = structlog.get_logger(__name__)

TIMEFRAMES = ["M15", "H1", "H4", "D1"]
BACKFILL_MONTHS = 6
MAX_PAGINATION_ITERS = 200  # safety cap: 6 months M15 = ~36 pages of 500


class CandleFetcher:
    """Fetches FXCM candles via MetaAPI and stores them via upsert in PostgreSQL."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = MarketDataClient(self.settings)

    def _parse_candle(self, raw: dict, instrument: str, timeframe: str) -> Candle | None:
        """Parse a normalized MetaAPI candle dict into a Candle ORM object.

        Returns None if the candle is missing required fields (malformed).
        """
        try:
            if not all(k in raw for k in ("open", "high", "low", "close")):
                log.warning("candle_fetcher.malformed_candle", raw_keys=list(raw.keys()))
                return None
            ts_str = raw.get("time")
            if not ts_str:
                log.warning("candle_fetcher.missing_timestamp")
                return None
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return Candle(
                instrument=instrument,
                timeframe=timeframe,
                timestamp=ts,
                open=Decimal(str(raw["open"])),
                high=Decimal(str(raw["high"])),
                low=Decimal(str(raw["low"])),
                close=Decimal(str(raw["close"])),
                volume=int(raw.get("tickVolume", raw.get("volume", 0))),
                complete=True,  # MetaAPI historical candles are always complete
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("candle_fetcher.parse_error", error=str(exc))
            return None

    async def fetch_and_store(
        self,
        instrument: str = "XAUUSD",
        timeframe: str = "M15",
        count: int = 500,
        from_time: str | None = None,
    ) -> int:
        """Fetch candles and upsert into PostgreSQL.

        Uses INSERT ... ON CONFLICT (instrument, timeframe, timestamp) DO NOTHING.

        Args:
            instrument: Trading instrument, e.g. "XAUUSD"
            timeframe: One of "M15", "H1", "H4", "D1"
            count: Number of candles to fetch (ignored when from_time is set)
            from_time: ISO 8601 UTC string — if set, fetches from this time onward

        Returns:
            Number of rows inserted (conflicts silently skipped).
        """
        raw_candles = await self.client.get_candles(
            instrument=instrument,
            granularity=timeframe,
            count=count,
            from_time=from_time,
        )

        if not raw_candles:
            return 0

        candles = [
            c for raw in raw_candles
            if (c := self._parse_candle(raw, instrument, timeframe)) is not None
        ]

        if not candles:
            return 0

        stmt = pg_insert(Candle).values(
            [
                {
                    "instrument": c.instrument,
                    "timeframe": c.timeframe,
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                    "complete": c.complete,
                }
                for c in candles
            ]
        ).on_conflict_do_nothing(
            index_elements=["instrument", "timeframe", "timestamp"]
        )

        async with AsyncSessionLocal() as session:
            result = await session.execute(stmt)
            await session.commit()
            inserted = result.rowcount if result.rowcount is not None else 0

        log.info(
            "candle_fetcher.stored",
            instrument=instrument,
            timeframe=timeframe,
            fetched=len(raw_candles),
            parsed=len(candles),
            inserted=inserted,
        )
        return inserted

    async def backfill_timeframe(
        self,
        instrument: str = "XAUUSD",
        timeframe: str = "M15",
    ) -> int:
        """Backfill 6 months of candles for a single timeframe via paginated from_time fetches.

        Paginates forward from (now - 6 months) until the API returns an empty list
        or MAX_PAGINATION_ITERS is reached.

        Args:
            instrument: Trading instrument, e.g. "XAUUSD"
            timeframe: One of "M15", "H1", "H4", "D1"

        Returns:
            Total candles inserted.
        """
        start_dt = datetime.now(timezone.utc) - timedelta(days=BACKFILL_MONTHS * 30)
        from_time = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        total_inserted = 0
        iterations = 0

        log.info(
            "candle_fetcher.backfill_start",
            instrument=instrument,
            timeframe=timeframe,
            from_time=from_time,
        )

        while iterations < MAX_PAGINATION_ITERS:
            raw_candles = await self.client.get_candles(
                instrument=instrument,
                granularity=timeframe,
                from_time=from_time,
            )

            if not raw_candles:
                break  # API returned empty — backfill complete for this timeframe

            inserted = await self.fetch_and_store(
                instrument=instrument,
                timeframe=timeframe,
                from_time=from_time,
            )
            total_inserted += inserted
            iterations += 1

            # Advance from_time to just after the last candle's timestamp
            last_ts_str = raw_candles[-1].get("time", "")
            if not last_ts_str:
                break
            last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
            from_time = (last_ts + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Stop if last page returned fewer than 500 — we've reached the present
            if len(raw_candles) < 500:
                break

        log.info(
            "candle_fetcher.backfill_complete",
            instrument=instrument,
            timeframe=timeframe,
            total_inserted=total_inserted,
            iterations=iterations,
        )
        return total_inserted

    async def backfill_all(self, instrument: str = "XAUUSD") -> None:
        """Backfill all 4 timeframes sequentially.

        Called once on startup. Runs as a background task so it does not
        block the FastAPI lifespan yield.

        Args:
            instrument: Trading instrument, e.g. "XAUUSD"
        """
        log.info("candle_fetcher.backfill_all_start", instrument=instrument)
        for timeframe in TIMEFRAMES:
            try:
                await self.backfill_timeframe(instrument=instrument, timeframe=timeframe)
            except Exception as exc:
                log.error(
                    "candle_fetcher.backfill_timeframe_failed",
                    instrument=instrument,
                    timeframe=timeframe,
                    error=str(exc),
                )
        log.info("candle_fetcher.backfill_all_complete", instrument=instrument)

    async def prune_incomplete(self, instrument: str = "XAUUSD") -> int:
        """Delete candles with complete=False that are older than 24 hours.

        Per CLAUDE.md 8.3 — incomplete candles accumulate during market hours
        and must be pruned to avoid stale data.

        Args:
            instrument: Trading instrument, e.g. "XAUUSD"

        Returns:
            Number of rows deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        stmt = delete(Candle).where(
            Candle.instrument == instrument,
            Candle.complete.is_(False),
            Candle.timestamp < cutoff,
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(stmt)
            await session.commit()
            deleted = result.rowcount or 0

        if deleted > 0:
            log.info("candle_fetcher.pruned_incomplete", deleted=deleted, cutoff=str(cutoff))
        return deleted
