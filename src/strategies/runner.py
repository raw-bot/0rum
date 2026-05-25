"""StrategyRunner — loads active params from DB, fetches candles, runs all 4 strategies in parallel.

Per CLAUDE.md §9:
- D-04: asyncio.gather() for parallel strategy execution.
- D-05: midpoint fallback when no active optimizer result exists.
- D-06: exactly 500 candles per timeframe per run.
- D-07: returns list[CandidateSignal] in-memory only — no DB writes.
- D-08: no APScheduler wiring — scheduling is Phase 4's responsibility.
"""

import asyncio

import structlog
from sqlalchemy import func, select

from src.database import AsyncSessionLocal
from src.models.candle import Candle
from src.models.optimizer_result import OptimizerResultORM
from src.models.signal_data import CandidateSignal, StrategyName

log = structlog.get_logger(__name__)

TIMEFRAMES = ["M15", "H1", "H4", "D1"]
CANDLES_PER_TIMEFRAME = 500  # per D-06: uniform 500-candle window across all timeframes
INSTRUMENT = "XAUUSD"


def validate_params_against_ranges(
    params: dict,
    ranges: dict[str, tuple[float, float]],
) -> dict:
    """Validate optimizer params against the strategy-declared ranges."""
    validated = {}
    for name, (lo, hi) in ranges.items():
        try:
            value = float(params[name])
        except KeyError as exc:
            raise ValueError(f"Missing required parameter {name}") from exc
        if value < lo or value > hi:
            raise ValueError(f"Parameter {name}={value} outside range [{lo}, {hi}]")
        validated[name] = value
    return validated


class StrategyRunner:
    """Coordinates all 4 strategies: loads params, fetches candles, runs in parallel.

    Per D-04: Uses asyncio.gather() to run all strategies concurrently.
    Per D-05: Falls back to midpoint of PARAM_RANGES if no active optimizer result.
    Per D-06: Fetches exactly 500 candles per timeframe per run.
    Per D-07: Returns list[CandidateSignal] in-memory only — no DB writes.
    Per D-08: No APScheduler wiring here — scheduling is Phase 4's responsibility.
    """

    def __init__(self) -> None:
        """Initialise StrategyRunner.

        Strategy imports are deferred to run() to avoid circular imports at module
        load time (strategies import from base, which is in the same package).
        """
        pass

    async def _load_active_params(
        self, strategy_name: str, param_ranges: dict
    ) -> dict | None:
        """Load active optimizer params for a strategy.

        Queries optimizer_results WHERE is_active=TRUE AND strategy=<strategy_name>,
        orders by created_at DESC, takes the most recent row.

        Per D-05: If the optimizer has never produced any result, computes
        midpoints of PARAM_RANGES and logs at INFO level with event key
        "strategy_runner.fallback_to_midpoints".

        After Phase 5 validation has run, a missing active row means the strategy
        has not passed validation. In that case return None so the runner skips
        the strategy instead of generating signals with unvalidated midpoint params.

        Per threat T-03-01: Caller (strategy.generate_signals) is responsible for
        validating param keys and values are within their declared PARAM_RANGES before use.

        Args:
            strategy_name: Strategy name string (e.g. "liquidity_sweep") — must match
                           the strategy column in optimizer_results.
            param_ranges: Strategy's PARAM_RANGES dict for midpoint computation.

        Returns:
            Dict of param_name → value from DB or computed midpoints, or None
            when optimizer history exists but this strategy has no active row.
        """
        optimizer_result_count = 0
        async with AsyncSessionLocal() as session:
            stmt = (
                select(OptimizerResultORM)
                .where(
                    OptimizerResultORM.is_active.is_(True),
                    OptimizerResultORM.strategy == strategy_name,
                )
                .order_by(OptimizerResultORM.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

            if row is None:
                optimizer_result_count = int(
                    (
                        await session.execute(
                            select(func.count()).select_from(OptimizerResultORM)
                        )
                    ).scalar_one()
                )

        if row is not None:
            return validate_params_against_ranges(dict(row.params), param_ranges)

        if optimizer_result_count > 0:
            log.info(
                "strategy_runner.skipped_unvalidated_strategy",
                strategy=strategy_name,
            )
            return None

        # D-05: No active params — compute midpoint of each PARAM_RANGES entry.
        # midpoint = (min + max) / 2 for each optimisable parameter.
        midpoints = {
            param: (lo + hi) / 2.0
            for param, (lo, hi) in param_ranges.items()
        }
        log.info(
            "strategy_runner.fallback_to_midpoints",
            strategy=strategy_name,
            midpoints=midpoints,
        )
        return validate_params_against_ranges(midpoints, param_ranges)

    async def _fetch_candles(self) -> dict[str, list]:
        """Fetch the most recent 500 complete candles per timeframe from the DB.

        Per D-06: Exactly CANDLES_PER_TIMEFRAME (500) candles are fetched per timeframe.
        Candles are filtered to complete=True and ordered oldest→newest for indicator
        calculations (argrelextrema and ATR require chronological ordering).

        Returns:
            Dict keyed by timeframe ("M15", "H1", "H4", "D1"),
            value is list of Candle ORM objects ordered oldest→newest.
        """
        candles: dict[str, list] = {}
        async with AsyncSessionLocal() as session:
            for tf in TIMEFRAMES:
                stmt = (
                    select(Candle)
                    .where(
                        Candle.instrument == INSTRUMENT,
                        Candle.timeframe == tf,
                        Candle.complete.is_(True),
                    )
                    .order_by(Candle.timestamp.desc())
                    .limit(CANDLES_PER_TIMEFRAME)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                # Reverse to oldest→newest order for indicator calculations
                candles[tf] = list(reversed(rows))

        log.info(
            "strategy_runner.candles_fetched",
            counts={tf: len(c) for tf, c in candles.items()},
        )
        return candles

    async def run(self) -> list[CandidateSignal]:
        """Run all 4 strategies in parallel and aggregate CandidateSignals.

        Per D-04: asyncio.gather() is used twice:
        1. To load DB params for all 4 strategies in parallel.
        2. To run all 4 strategy.generate_signals() calls in parallel.

        Per D-07: No session.add(), session.commit(), or session.merge() calls occur.
        All signals are returned in-memory for the pipeline (Phase 4) to persist.

        Returns:
            Combined list[CandidateSignal] from all strategies. Empty list if
            all strategies return no signals — not an error condition.

        Note:
            Strategy exceptions are caught individually via return_exceptions=True.
            A single strategy failure does not abort other strategies (T-03-04 mitigation).
            Each exception is logged with strategy name and error string.
        """
        # Import here to avoid circular imports — strategies import from base (same pkg)
        from src.strategies.liquidity_sweep import LiquiditySweepStrategy
        from src.strategies.trend_continuation import TrendContinuationStrategy
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy
        from src.strategies.ema_momentum import EmaMomentumStrategy

        strategy_classes = [
            LiquiditySweepStrategy,
            TrendContinuationStrategy,
            BreakoutExpansionStrategy,
            EmaMomentumStrategy,
        ]

        candles = await self._fetch_candles()

        # Load params for each strategy (DB row or midpoint fallback) — D-04 parallel
        params_list = await asyncio.gather(
            *[
                self._load_active_params(
                    cls.STRATEGY_NAME,  # class-level constant, e.g. "liquidity_sweep"
                    cls.PARAM_RANGES,
                )
                for cls in strategy_classes
            ]
        )

        # Instantiate only strategies with validated params, or bootstrap midpoints
        # before the first optimizer result exists.
        instances = [
            cls(params=p)
            for cls, p in zip(strategy_classes, params_list)
            if p is not None
        ]

        if not instances:
            log.info("strategy_runner.no_validated_strategies")
            return []

        # Run all 4 strategies concurrently — D-04: asyncio.gather with exception capture
        results = await asyncio.gather(
            *[strategy.generate_signals(candles) for strategy in instances],
            return_exceptions=True,
        )

        all_signals: list[CandidateSignal] = []
        for strategy, result in zip(instances, results):
            if isinstance(result, Exception):
                # T-03-04 mitigation: log individually, don't re-raise
                log.error(
                    "strategy_runner.strategy_failed",
                    strategy=strategy.__class__.__name__,
                    error=str(result),
                )
            else:
                all_signals.extend(result)

        log.info(
            "strategy_runner.run_complete",
            total_signals=len(all_signals),
            per_strategy={
                s.__class__.__name__: len(r) if not isinstance(r, Exception) else 0
                for s, r in zip(instances, results)
            },
        )
        return all_signals
