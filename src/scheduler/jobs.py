"""APScheduler job definitions for candle refresh across 4 timeframes."""

import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any

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

# Injected service singletons — set by main.py at startup.
# Tests can inject mocks via _set_pipeline_runner().
_breaker_manager: "Any | None" = None
_pipeline_runner: "Any | None" = None


def _set_pipeline_runner(runner: "Any") -> None:
    """Inject PipelineRunner singleton with wired router. Called once at app startup."""
    global _pipeline_runner
    _pipeline_runner = runner


def get_last_candle_fetch() -> dict[str, str | None]:
    """Return a copy of the last fetch timestamps for health endpoint wiring."""
    return dict(_last_candle_fetch)


def _as_utc_aware(dt: datetime | None) -> datetime | None:
    """Normalize DB datetimes for safe ordering comparisons."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _refresh_timeframe(timeframe: str) -> None:
    """Fetch latest candles for one timeframe, run gap detection, update last_fetch."""
    from src.config import MarketDataProvider
    settings = get_settings()
    max_gap_bars = (
        settings.ig_max_gap_bars
        if settings.market_data_provider == MarketDataProvider.IG
        else None
    )
    async with CandleFetcher(settings=settings) as fetcher:
        detector = GapDetector(fetcher=fetcher, max_gap_bars=max_gap_bars)
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


async def run_optimizer() -> None:
    """Run walk-forward optimizer for all 4 strategies — called every 24h.

    Deferred import of WalkForwardOptimizer mirrors the run_pipeline() pattern.
    max_instances=1 on the scheduler job prevents concurrent optimizer runs
    (each run can take several minutes on a full 3-window evaluation).
    """
    from src.backtesting.optimizer import WalkForwardOptimizer
    try:
        optimizer = WalkForwardOptimizer()
        await optimizer.run()
        log.info("jobs.optimizer.complete")
    except Exception as exc:
        log.error("jobs.optimizer.failed", error=str(exc))


async def run_pipeline() -> None:
    """Run strategies then signal pipeline — called every 15 minutes (per D-01).

    Sequence (inline, no Redis queue per D-01):
      1. StrategyRunner.run() → list[CandidateSignal]
      2. Fetch latest 200 H1 candles from DB for regime detection
      3. PipelineRunner.run(candidates, h1_candles) → list[ApprovedSignalORM]
    """
    from src.strategies.runner import StrategyRunner
    from src.models.candle import Candle
    from src.database import AsyncSessionLocal
    from sqlalchemy import select

    try:
        # Step 1: Run all 4 strategies
        candidates = await StrategyRunner().run()
        log.info("jobs.pipeline.strategies_done", candidate_count=len(candidates))

        if not candidates:
            log.info("jobs.pipeline.no_candidates")
            return

        # Step 2: Fetch H1 candles for regime detection (200 candles ≈ 8+ days, sufficient for ADX/EMA)
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Candle)
                .where(
                    Candle.instrument == "XAUUSD",
                    Candle.timeframe == "H1",
                    Candle.complete.is_(True),
                )
                .order_by(Candle.timestamp.desc())
                .limit(200)
            )
            result = await session.execute(stmt)
            h1_rows = result.scalars().all()
            h1_candles = list(reversed(h1_rows))  # oldest→newest for indicator calculation

        log.info("jobs.pipeline.h1_fetched", count=len(h1_candles))

        # Step 3: Run full pipeline — use injected runner if available (D-15).
        from src.pipeline.runner import PipelineRunner
        pipeline = _pipeline_runner or PipelineRunner()
        approved = await pipeline.run(candidates=candidates, h1_candles=h1_candles)
        log.info("jobs.pipeline.done", approved_count=len(approved))

    except Exception as exc:
        log.error("jobs.pipeline.failed", error=str(exc))


async def monitor_trades() -> None:
    """Monitor open theoretical trades every 15 min — check price levels and update status.

    Per D-01: runs independently of run_pipeline. Evaluates latest completed M15 candle
    HIGH/LOW range for intra-candle level touches.

    Per D-02: Uses H1 ATR(14) for trailing stop distance (1.0 × ATR(H1)).
    Per D-03: Trailing stop ratchets — only updates when strictly better.
    Per D-04: After TP1_HIT, both TP2 and trailing stop are active simultaneously.
    Per D-05: SL wins pre-TP1; trailing stop wins post-TP1 on tie.
    Per D-07: BreakerManager.record_stop() called inline after SL close.
    Per D-08: BreakerManager.record_win() called when pnl_pct > 0 on blended close.
    Per D-17: strategy_stats upsert in same transaction as TradeORM status change.
    """
    from sqlalchemy import select
    from src.backtesting.regime_detector import RegimeDetector
    from src.database import AsyncSessionLocal
    from src.models.candle import Candle
    from src.models.signal import ApprovedSignalORM, CandidateSignalORM
    from src.models.trade import TradeORM

    try:
        async with AsyncSessionLocal() as session:
            # 1. Fetch latest completed M15 candle
            m15_stmt = (
                select(Candle)
                .where(
                    Candle.instrument == "XAUUSD",
                    Candle.timeframe == "M15",
                    Candle.complete.is_(True),
                )
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )
            m15_result = await session.execute(m15_stmt)
            latest_m15 = m15_result.scalar_one_or_none()
            if latest_m15 is None:
                log.info("jobs.monitor_trades.no_candle")
                return

            candle_high = Decimal(str(latest_m15.high))
            candle_low = Decimal(str(latest_m15.low))
            candle_timestamp = _as_utc_aware(latest_m15.timestamp)

            # 2. Fetch last 20 H1 candles for ATR computation
            h1_stmt = (
                select(Candle)
                .where(
                    Candle.instrument == "XAUUSD",
                    Candle.timeframe == "H1",
                    Candle.complete.is_(True),
                )
                .order_by(Candle.timestamp.desc())
                .limit(20)
            )
            h1_result = await session.execute(h1_stmt)
            h1_rows = list(reversed(h1_result.scalars().all()))
            atr_h1 = Decimal("0")
            if len(h1_rows) >= 14:
                atr_val = RegimeDetector()._calculate_atr(h1_rows, period=14)
                atr_h1 = Decimal(str(atr_val))

            # 3. Fetch all open trades with strategy name via JOIN (D-18)
            trades_stmt = (
                select(TradeORM, CandidateSignalORM.strategy)
                .join(ApprovedSignalORM, TradeORM.approved_signal_id == ApprovedSignalORM.id)
                .join(
                    CandidateSignalORM,
                    ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id,
                )
                .where(TradeORM.status.in_(["OPEN", "TP1_HIT"]))
            )
            trades_result = await session.execute(trades_stmt)
            open_trades = trades_result.all()  # list of (TradeORM, strategy_str) rows

        if not open_trades:
            log.info("jobs.monitor_trades.no_open_trades")
            return

        # 4. Process each trade
        for trade_row, strategy_name in open_trades:
            trade_opened_at = _as_utc_aware(getattr(trade_row, "opened_at", None))
            if (
                candle_timestamp is not None
                and trade_opened_at is not None
                and candle_timestamp <= trade_opened_at
            ):
                log.info(
                    "jobs.monitor_trades.skipped_pre_open_candle",
                    trade_id=str(trade_row.id),
                    candle_timestamp=candle_timestamp.isoformat(),
                    opened_at=trade_opened_at.isoformat(),
                )
                continue
            await _process_trade(
                trade=trade_row,
                strategy_name=strategy_name,
                candle_high=candle_high,
                candle_low=candle_low,
                atr_h1=atr_h1,
            )

        log.info("jobs.monitor_trades.complete", checked=len(open_trades))

    except Exception as exc:
        log.error("jobs.monitor_trades.failed", error=str(exc))


async def _process_trade(
    trade: "Any",
    strategy_name: str,
    candle_high: Decimal,
    candle_low: Decimal,
    atr_h1: Decimal,
) -> None:
    """Evaluate one trade against the latest M15 candle; update DB if status changes."""
    from src.database import AsyncSessionLocal

    direction = trade.direction  # "BUY" or "SELL"
    entry = Decimal(str(trade.entry_price))
    sl = Decimal(str(trade.sl_price))
    tp1 = Decimal(str(trade.tp1_price))
    tp2 = Decimal(str(trade.tp2_price)) if trade.tp2_price is not None else None
    direction_sign = Decimal("1") if direction == "BUY" else Decimal("-1")

    # Direction-aware touch logic
    def sl_touched() -> bool:
        return candle_low <= sl if direction == "BUY" else candle_high >= sl

    def tp1_touched() -> bool:
        return candle_high >= tp1 if direction == "BUY" else candle_low <= tp1

    def trail_touched() -> bool:
        if trade.trailing_stop_price is None:
            return False
        trail = Decimal(str(trade.trailing_stop_price))
        return candle_low <= trail if direction == "BUY" else candle_high >= trail

    def tp2_touched() -> bool:
        if tp2 is None:
            return False
        return candle_high >= tp2 if direction == "BUY" else candle_low <= tp2

    if trade.status == "OPEN":
        sl_hit = sl_touched()
        tp1_hit = tp1_touched()

        if sl_hit:  # D-05: SL wins on tie (sl_hit covers both sl_hit and sl_hit+tp1_hit)
            await _close_trade(trade, strategy_name, "SL", sl, entry, tp1, direction_sign)
        elif tp1_hit:
            # Transition to TP1_HIT, initialize trailing stop
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    trade.status = "TP1_HIT"
                    # Initialize trailing stop at TP1 price ± ATR(H1)
                    if atr_h1 > Decimal("0"):
                        if direction == "BUY":
                            trade.trailing_stop_price = tp1 - atr_h1
                        else:
                            trade.trailing_stop_price = tp1 + atr_h1
                    session.add(trade)
            log.info(
                "monitor.tp1_hit",
                trade_id=str(trade.id),
                strategy=strategy_name,
                direction=direction,
            )

    elif trade.status == "TP1_HIT":
        # Update trailing stop ratchet first
        if atr_h1 > Decimal("0"):
            if direction == "BUY":
                # Trail follows candle high
                new_trail = candle_high - atr_h1
                stored = Decimal(str(trade.trailing_stop_price)) if trade.trailing_stop_price else None
                if stored is None or new_trail > stored:
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            trade.trailing_stop_price = new_trail
                            session.add(trade)
            else:
                new_trail = candle_low + atr_h1
                stored = Decimal(str(trade.trailing_stop_price)) if trade.trailing_stop_price else None
                if stored is None or new_trail < stored:
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            trade.trailing_stop_price = new_trail
                            session.add(trade)

        trail_hit = trail_touched()
        tp2_hit = tp2_touched()

        if trail_hit:  # D-05: trailing stop wins on tie
            exit_price = Decimal(str(trade.trailing_stop_price)) if trade.trailing_stop_price else sl
            await _close_trade(trade, strategy_name, "TRAIL", exit_price, entry, tp1, direction_sign)
        elif tp2_hit and tp2 is not None:
            await _close_trade(trade, strategy_name, "TP2", tp2, entry, tp1, direction_sign)


async def _close_trade(
    trade: "Any",
    strategy_name: str,
    close_reason: str,
    exit_price: Decimal,
    entry_price: Decimal,
    tp1_price: Decimal,
    direction_sign: Decimal,
) -> None:
    """Close a trade: set status=CLOSED, compute blended P&L, upsert strategy_stats, call BreakerManager."""
    from sqlalchemy import select, update
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from src.database import AsyncSessionLocal
    from src.models.strategy_stats import StrategyStatsORM

    # D-06: Blended P&L after TP1; direct SL closes use the full-position loss.
    tp1_pnl = (tp1_price - entry_price) / entry_price * direction_sign
    final_pnl = (exit_price - entry_price) / entry_price * direction_sign
    if close_reason == "SL":
        blended_pnl_pct = final_pnl
    else:
        blended_pnl_pct = Decimal("0.5") * tp1_pnl + Decimal("0.5") * final_pnl

    is_win = blended_pnl_pct > Decimal("0")

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Update trade status
            trade.status = "CLOSED"
            trade.close_reason = close_reason
            trade.pnl_pct = blended_pnl_pct
            trade.closed_at = datetime.now(timezone.utc)
            session.add(trade)

            # D-17: Upsert strategy_stats in same transaction
            profit_part = blended_pnl_pct if is_win else Decimal("0")
            loss_part = abs(blended_pnl_pct) if not is_win else Decimal("0")

            upsert_stmt = pg_insert(StrategyStatsORM).values(
                strategy=strategy_name,
                trade_count=1,
                wins=1 if is_win else 0,
                losses=0 if is_win else 1,
                gross_profit_pct=profit_part,
                gross_loss_pct=loss_part,
                total_pnl_pct=blended_pnl_pct,
                win_rate=Decimal("1") if is_win else Decimal("0"),
                profit_factor=Decimal("0"),
                updated_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=["strategy"],
                set_={
                    "trade_count": StrategyStatsORM.trade_count + 1,
                    "wins": StrategyStatsORM.wins + (1 if is_win else 0),
                    "losses": StrategyStatsORM.losses + (0 if is_win else 1),
                    "gross_profit_pct": StrategyStatsORM.gross_profit_pct + profit_part,
                    "gross_loss_pct": StrategyStatsORM.gross_loss_pct + loss_part,
                    "total_pnl_pct": StrategyStatsORM.total_pnl_pct + blended_pnl_pct,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await session.execute(upsert_stmt)

            # Recompute win_rate and profit_factor in same transaction after upsert
            stats_row = await session.execute(
                select(StrategyStatsORM).where(StrategyStatsORM.strategy == strategy_name)
            )
            stats = stats_row.scalar_one_or_none()
            if stats is not None and stats.trade_count > 0:
                new_win_rate = Decimal(str(stats.wins)) / Decimal(str(stats.trade_count))
                if stats.gross_loss_pct > Decimal("0"):
                    new_pf = stats.gross_profit_pct / stats.gross_loss_pct
                else:
                    # Sentinel: no losses yet
                    new_pf = Decimal("0") if stats.wins == 0 else Decimal("999.9999")
                await session.execute(
                    update(StrategyStatsORM)
                    .where(StrategyStatsORM.strategy == strategy_name)
                    .values(win_rate=new_win_rate, profit_factor=new_pf)
                )

    log.info(
        "monitor.trade_closed",
        trade_id=str(trade.id),
        strategy=strategy_name,
        close_reason=close_reason,
        pnl_pct=str(blended_pnl_pct),
    )

    # D-07: BreakerManager.record_stop() inline after SL close
    # D-08: BreakerManager.record_win() when blended_pnl_pct > 0
    from src.risk.breaker import BreakerManager
    breaker = _breaker_manager or BreakerManager()
    if close_reason == "SL":
        await breaker.record_stop(trade_id=trade.id, strategy=strategy_name)
    if is_win:
        await breaker.record_win()


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the APScheduler instance with all 4 candle jobs.

    Cadences (CLAUDE.md section 8.2):
      M15 → every 15 minutes
      H1  → every 1 hour
      H4  → every 4 hours
      D1  → daily at 00:05 UTC

    All jobs use max_instances=1 to prevent job pile-up on slow fetches.
    """
    settings = get_settings()
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

    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(minutes=15),
        id="run_pipeline",
        name="Run strategies and signal pipeline every 15 minutes",
        max_instances=1,
        replace_existing=True,
    )

    scheduler.add_job(
        run_optimizer,
        trigger=IntervalTrigger(hours=settings.optimizer_interval_hours),
        id="run_optimizer",
        name="Run walk-forward optimizer every 24h",
        max_instances=1,
        replace_existing=True,
    )

    scheduler.add_job(
        monitor_trades,
        trigger=IntervalTrigger(minutes=15),
        id="monitor_trades",
        name="Monitor open theoretical trades every 15 minutes",
        max_instances=1,
        replace_existing=True,
    )

    return scheduler
