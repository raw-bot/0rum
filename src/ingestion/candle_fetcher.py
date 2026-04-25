"""Candle fetcher — fetch from the configured provider and store in PostgreSQL."""

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
MAX_PAGINATION_ITERS = 200  # safety cap: 6 months M15 = ~18 pages of 1000

# Minutes per bar for each supported timeframe.
TF_INTERVAL_MINUTES: dict[str, int] = {
    "M15": 15,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

# Maximum bars to request per API call.  IG demo caps single-request windows;
# 1 000 bars keeps every chunk comfortably inside that limit.
CHUNK_BARS = 1000

# Per-timeframe bar counts for IG-light startup warm-up.
# Mirrors config defaults; overridden at runtime via Settings when present.
_IG_WARMUP_BARS_DEFAULT: dict[str, str] = {
    "M15": "ig_warmup_bars_m15",
    "H1": "ig_warmup_bars_h1",
    "H4": "ig_warmup_bars_h4",
    "D1": "ig_warmup_bars_d1",
}


class CandleFetcher:
    """Fetches provider candles and stores them via upsert in PostgreSQL."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = MarketDataClient(self.settings)

    async def aclose(self) -> None:
        """Close the underlying MarketDataClient and its network resources."""
        await self.client.aclose()

    async def __aenter__(self) -> "CandleFetcher":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _parse_candle(self, raw: dict, instrument: str, timeframe: str) -> Candle | None:
        """Parse a normalized provider candle dict into a Candle ORM object.

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
                complete=True,  # Historical fetches are treated as complete bars
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
        to_time: str | None = None,
        raw_candles: list[dict] | None = None,
    ) -> int:
        """Fetch candles and upsert into PostgreSQL.

        Uses INSERT ... ON CONFLICT (instrument, timeframe, timestamp) DO NOTHING.

        Args:
            instrument: Trading instrument, e.g. "XAUUSD"
            timeframe: One of "M15", "H1", "H4", "D1"
            count: Number of candles to fetch (ignored when from_time is set)
            from_time: ISO 8601 UTC string — if set, fetches from this time onward
            to_time: Optional ISO 8601 UTC string to bound the fetch when supported
            raw_candles: Optional prefetched normalized candles to avoid a duplicate
                provider call during backfill loops

        Returns:
            Number of rows inserted (conflicts silently skipped).
        """
        if raw_candles is None:
            raw_candles = await self.client.get_candles(
                instrument=instrument,
                granularity=timeframe,
                count=count,
                from_time=from_time,
                to_time=to_time,
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
        start_dt: datetime | None = None,
        end_dt: datetime | None = None,
    ) -> int:
        """Backfill candles for a single timeframe using explicit bounded chunks.

        Each API call is bounded to [cursor, cursor + chunk_window] so the provider
        never receives an open-ended range that exceeds per-request data allowances.
        The cursor advances by chunk_window each iteration until it reaches end_dt.

        Args:
            instrument: Trading instrument, e.g. "XAUUSD"
            timeframe: One of "M15", "H1", "H4", "D1"
            start_dt: Backfill start (UTC).  Defaults to now - 6 months.
            end_dt: Backfill end (UTC).  Defaults to now.

        Returns:
            Total candles inserted.
        """
        now = datetime.now(timezone.utc)
        if start_dt is None:
            start_dt = now - timedelta(days=BACKFILL_MONTHS * 30)
        if end_dt is None:
            end_dt = now

        interval_minutes = TF_INTERVAL_MINUTES.get(timeframe, 15)
        chunk_window = timedelta(minutes=interval_minutes * CHUNK_BARS)

        total_inserted = 0
        iterations = 0
        cursor = start_dt

        log.info(
            "candle_fetcher.backfill_start",
            instrument=instrument,
            timeframe=timeframe,
            from_time=cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            to_time=end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            chunk_bars=CHUNK_BARS,
        )

        while cursor < end_dt and iterations < MAX_PAGINATION_ITERS:
            chunk_end = min(cursor + chunk_window, end_dt)
            from_time = cursor.strftime("%Y-%m-%dT%H:%M:%SZ")
            to_time = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")

            raw_candles = await self.client.get_candles(
                instrument=instrument,
                granularity=timeframe,
                from_time=from_time,
                to_time=to_time,
            )

            if raw_candles:
                inserted = await self.fetch_and_store(
                    instrument=instrument,
                    timeframe=timeframe,
                    from_time=from_time,
                    to_time=to_time,
                    raw_candles=raw_candles,
                )
                total_inserted += inserted

            iterations += 1
            cursor = chunk_end

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

    async def warm_up_timeframe(
        self,
        instrument: str = "XAUUSD",
        timeframe: str = "M15",
    ) -> int:
        """Fetch a single bounded recent window for one timeframe (IG-safe startup).

        Issues exactly one fetch_and_store call using a count, not a date range,
        so no open-ended historical request is sent to IG.
        """
        setting_key = _IG_WARMUP_BARS_DEFAULT.get(timeframe, "ig_warmup_bars_m15")
        bars = getattr(self.settings, setting_key, 300)
        log.info(
            "candle_fetcher.warmup_start",
            instrument=instrument,
            timeframe=timeframe,
            bars=bars,
        )
        inserted = await self.fetch_and_store(
            instrument=instrument,
            timeframe=timeframe,
            count=bars,
        )
        log.info(
            "candle_fetcher.warmup_complete",
            instrument=instrument,
            timeframe=timeframe,
            inserted=inserted,
        )
        return inserted

    async def warm_up_all(self, instrument: str = "XAUUSD") -> None:
        """Warm up all 4 timeframes with one bounded fetch each.

        Replaces backfill_all() on the IG path — never issues a multi-page
        date-range walk.
        """
        log.info("candle_fetcher.warmup_all_start", instrument=instrument)
        for timeframe in TIMEFRAMES:
            try:
                await self.warm_up_timeframe(instrument=instrument, timeframe=timeframe)
            except Exception as exc:
                log.error(
                    "candle_fetcher.warmup_timeframe_failed",
                    instrument=instrument,
                    timeframe=timeframe,
                    error=str(exc),
                )
        log.info("candle_fetcher.warmup_all_complete", instrument=instrument)

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
