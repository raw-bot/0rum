"""Walk-forward optimizer — LHS sampling, backtesting, WFE gating, DB writes.

Architecture (per RESEARCH.md Sync/Async Decision):
  async entry (run()) → sync heavy compute (LHS, trade simulation) → async DB write.
  No asyncio.run() anywhere — the APScheduler job is already a coroutine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog
from scipy.stats.qmc import LatinHypercube, scale
from sqlalchemy import select, update

from src.backtesting.monte_carlo import monte_carlo_passes, run_monte_carlo
from src.backtesting.walk_forward import (
    MIN_CANDLES_FOR_OPTIMIZER,
    TradeOutcome,
    WalkForwardWindow,
    _check_sufficient_data,
    _compute_profit_factor,
    _compute_wfe,
    build_windows,
    multi_window_gate_passes,
    simulate_trade_outcome,
)
from src.config import get_settings
from src.database import AsyncSessionLocal
from src.models.candle import Candle
from src.models.optimizer_result import OptimizerResultORM

log = structlog.get_logger(__name__)

INSTRUMENT = "XAUUSD"
TIMEFRAMES = ["M15", "H1", "H4", "D1"]
BACKTEST_WINDOW_CANDLES = 500
BACKTEST_STEP_CANDLES = 1  # step one H1 candle per window position — per plan 05-02 spec
MIN_TRADES_FOR_EVALUATION = 5
N_WALK_FORWARD_WINDOWS = 3


class WalkForwardOptimizer:
    """Orchestrates LHS sampling, walk-forward backtesting, and DB param activation.

    Usage:
        optimizer = WalkForwardOptimizer()
        await optimizer.run()

    Designed to be called from an async APScheduler job. All DB access is
    async. All computation (LHS, trade simulation, Monte Carlo) is sync.

    The data guard (_check_sufficient_data) prevents param activation when
    the DB does not yet contain sufficient candles for a meaningful 3-window
    walk-forward evaluation.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def _sample_param_combinations(
        self,
        param_ranges: dict[str, tuple[float, float]],
        n_combos: int,
    ) -> list[dict[str, float]]:
        """Generate n_combos LHS parameter combinations for one strategy.

        Uses LatinHypercube(d=N_PARAMS) — NOT n_components= (deprecated before
        scipy 1.10; d= is correct in scipy 1.17.1). Values are clamped to
        declared bounds to guard against float-precision edge values (ASVS V5).

        Args:
            param_ranges: Strategy PARAM_RANGES dict mapping name → (lo, hi).
            n_combos: Number of combinations (100 per CONTEXT.md locked decision).

        Returns:
            List of n_combos dicts, each mapping param_name → clamped float value.
        """
        param_names = list(param_ranges.keys())
        d = len(param_names)
        l_bounds = [param_ranges[k][0] for k in param_names]
        u_bounds = [param_ranges[k][1] for k in param_names]

        sampler = LatinHypercube(d=d)
        unit_samples = sampler.random(n=n_combos)
        scaled_samples = scale(unit_samples, l_bounds, u_bounds)

        combos = []
        for i in range(n_combos):
            combo: dict[str, float] = {}
            for j, name in enumerate(param_names):
                lo, hi = l_bounds[j], u_bounds[j]
                raw = float(scaled_samples[i, j])
                combo[name] = max(lo, min(hi, raw))  # clamp — ASVS V5 guard
            combos.append(combo)
        return combos

    async def _fetch_all_candles(self) -> dict[str, list]:
        """Fetch ALL complete candles for XAUUSD across all timeframes from DB.

        Returns oldest-first per the CLAUDE.md oldest→newest invariant.
        The optimizer needs the full history to build 3-window walk-forward splits.

        Returns:
            Dict of timeframe → list[Candle ORM], oldest-first.
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
                    .order_by(Candle.timestamp.asc())
                )
                result = await session.execute(stmt)
                candles[tf] = list(result.scalars().all())
        log.info(
            "optimizer.candles_fetched",
            counts={tf: len(c) for tf, c in candles.items()},
        )
        return candles

    async def _run_strategy_backtest_async(
        self,
        strategy_instance: Any,
        candle_slice: dict[str, list],
    ) -> list[float]:
        """Slide a 500-candle window across candle_slice and collect trade P&L.

        Slides BACKTEST_WINDOW_CANDLES window across the H1 timeframe in
        BACKTEST_STEP_CANDLES increments. For each window
        position, calls strategy.generate_signals() with all 4 TFs aligned to
        the same date range. Simulates each signal's outcome using the 50
        subsequent H1 candles.

        Key contracts preserved:
        - Candle dict passed to strategy is oldest-first (CLAUDE.md invariant).
        - float() cast applied before numpy ops on Decimal ORM fields.
        - No DB access — candles pre-fetched.

        Args:
            strategy_instance: Instantiated strategy object with params already set.
            candle_slice: dict[str, list[Candle]] — all timeframes, oldest-first.

        Returns:
            List of float P&L values from simulated trade outcomes.
        """
        pnl_list: list[float] = []
        h1_candles = candle_slice.get("H1", [])
        n = len(h1_candles)

        if n < BACKTEST_WINDOW_CANDLES + 50:
            return pnl_list

        for start in range(0, n - BACKTEST_WINDOW_CANDLES - 50, BACKTEST_STEP_CANDLES):
            end = start + BACKTEST_WINDOW_CANDLES
            window_end_ts = h1_candles[end - 1].timestamp

            window_candles: dict[str, list] = {}
            for tf, all_tf_candles in candle_slice.items():
                tf_window = [c for c in all_tf_candles if c.timestamp <= window_end_ts]
                window_candles[tf] = tf_window[-BACKTEST_WINDOW_CANDLES:]

            try:
                signals = await strategy_instance.generate_signals(window_candles)
            except Exception as exc:
                log.debug(
                    "optimizer.backtest_signal_error",
                    strategy=strategy_instance.__class__.__name__,
                    error=str(exc),
                )
                continue

            outcome_candles = h1_candles[end:end + 50]
            for signal in signals:
                try:
                    direction = (
                        signal.direction.value
                        if hasattr(signal.direction, "value")
                        else signal.direction
                    )
                    _, pnl = simulate_trade_outcome(
                        direction=direction,
                        entry=float(signal.entry_price),
                        sl=float(signal.sl_price),
                        tp1=float(signal.tp1_price),
                        tp2=float(signal.tp2_price) if signal.tp2_price else None,
                        subsequent_candles=outcome_candles,
                    )
                    pnl_list.append(pnl)
                except Exception as exc:
                    log.debug("optimizer.outcome_sim_error", error=str(exc))

        return pnl_list

    async def _evaluate_all_combos(
        self,
        strategy_class: Any,
        windows: list[WalkForwardWindow],
        candles_by_tf: dict[str, list],
    ) -> dict[str, Any] | None:
        """Evaluate all 100 LHS combos for one strategy across all 3 WF windows.

        Returns the best combo (highest WFE among those passing WFE >= 0.50 AND
        multi-window gate), or None if no combo passes all gates.

        Args:
            strategy_class: The strategy class (uninstantiated).
            windows: 3 WalkForwardWindow objects, oldest-first.
            candles_by_tf: Full candle history, all TFs, oldest-first.

        Returns:
            Dict with combo and metrics, or None if no combo passes.
        """
        combos = self._sample_param_combinations(
            param_ranges=strategy_class.PARAM_RANGES,
            n_combos=self._settings.lhs_combos,
        )

        best: dict[str, Any] | None = None
        best_wfe: float = -1.0

        for combo in combos:
            oos_pfs: list[float] = []
            is_pf_last: float = 0.0
            oos_pf_last: float = 0.0
            all_oos_pnl: list[float] = []
            total_trades: int = 0

            for window in windows:
                is_slice: dict[str, list] = {
                    tf: [c for c in cs if window.train_start <= c.timestamp < window.train_end]
                    for tf, cs in candles_by_tf.items()
                }
                oos_slice: dict[str, list] = {
                    tf: [c for c in cs if window.oos_start <= c.timestamp < window.oos_end]
                    for tf, cs in candles_by_tf.items()
                }

                strategy = strategy_class(params=combo)

                is_pnl = await self._run_strategy_backtest_async(strategy, is_slice)
                oos_pnl = await self._run_strategy_backtest_async(strategy, oos_slice)

                if len(is_pnl) < MIN_TRADES_FOR_EVALUATION:
                    oos_pfs.append(0.0)
                    continue

                is_pf = _compute_profit_factor(np.array(is_pnl, dtype=float))
                oos_pf = _compute_profit_factor(np.array(oos_pnl, dtype=float))
                oos_pfs.append(oos_pf)

                is_pf_last = is_pf
                oos_pf_last = oos_pf
                total_trades += len(oos_pnl)
                all_oos_pnl.extend(oos_pnl)

            wfe = _compute_wfe(is_pf_last, oos_pf_last)

            if wfe < self._settings.wfe_minimum:
                continue

            if not multi_window_gate_passes(oos_pfs):
                continue

            if wfe > best_wfe:
                oos_arr = np.array(all_oos_pnl, dtype=float)
                winners = oos_arr[oos_arr > 0]
                win_rate = float(len(winners) / len(oos_arr)) if len(oos_arr) > 0 else 0.0
                equity = np.cumsum(oos_arr) if len(oos_arr) > 0 else np.array([0.0])
                running_max = np.maximum.accumulate(equity)
                max_dd = float((running_max - equity).max())

                best_wfe = wfe
                best = {
                    "combo": combo,
                    "best_wfe": wfe,
                    "is_score": is_pf_last,
                    "oos_score": oos_pf_last,
                    "oos_pf_per_window": oos_pfs,
                    "trade_count": total_trades,
                    "win_rate": win_rate,
                    "max_dd": max_dd,
                    "latest_window": windows[-1],
                    "all_oos_pnl": all_oos_pnl,
                }

        return best

    async def _activate_best_params(
        self,
        strategy_name: str,
        result: dict[str, Any],
    ) -> None:
        """Write the best combo to optimizer_results with is_active=True.

        Single transaction: deactivate existing active rows for this strategy,
        then insert new is_active=True row.

        Args:
            strategy_name: Strategy STRATEGY_NAME string.
            result: Dict from _evaluate_all_combos() containing combo and metrics.
        """
        window: WalkForwardWindow = result["latest_window"]

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(OptimizerResultORM)
                .where(
                    OptimizerResultORM.strategy == strategy_name,
                    OptimizerResultORM.is_active.is_(True),
                )
                .values(is_active=False)
            )

            new_result = OptimizerResultORM(
                strategy=strategy_name,
                params=result["combo"],
                train_start=window.train_start,
                train_end=window.train_end,
                test_start=window.oos_start,
                test_end=window.oos_end,
                in_sample_score=result["is_score"],
                oos_score=result["oos_score"],
                wfe=result["best_wfe"],
                profit_factor=result["oos_score"],
                max_drawdown=result["max_dd"],
                win_rate=result["win_rate"],
                trade_count=result["trade_count"],
                is_active=True,
            )
            session.add(new_result)
            await session.commit()

        log.info(
            "optimizer.params_activated",
            strategy=strategy_name,
            wfe=result["best_wfe"],
            trade_count=result["trade_count"],
        )

    async def run(self) -> None:
        """Run walk-forward optimizer for all 4 strategies.

        Sequence:
        1. Fetch all candles from DB (one query per timeframe).
        2. Check data sufficiency — skip and log if insufficient.
        3. Build 3 rolling walk-forward windows.
        4. For each strategy: evaluate 100 LHS combos against WFE + multi-window gates.
        5. For each strategy with a passing combo: run Monte Carlo, activate if passes.

        Strategies failing all gates retain their previous active params (no deactivation).
        """
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy
        from src.strategies.ema_momentum import EmaMomentumStrategy
        from src.strategies.liquidity_sweep import LiquiditySweepStrategy
        from src.strategies.trend_continuation import TrendContinuationStrategy

        strategy_classes = [
            LiquiditySweepStrategy,
            TrendContinuationStrategy,
            BreakoutExpansionStrategy,
            EmaMomentumStrategy,
        ]

        log.info("optimizer.run.start")

        candles_by_tf = await self._fetch_all_candles()

        if not _check_sufficient_data(candles_by_tf):
            log.warning(
                "optimizer.skipped.insufficient_data",
                counts={tf: len(c) for tf, c in candles_by_tf.items()},
                minimums=MIN_CANDLES_FOR_OPTIMIZER,
            )
            return

        most_recent_ts = max(
            candles_by_tf[tf][-1].timestamp
            for tf in TIMEFRAMES
            if candles_by_tf.get(tf)
        )

        windows = build_windows(
            most_recent_ts=most_recent_ts,
            train_months=self._settings.wf_train_months,
            test_months=self._settings.wf_test_months,
            n_windows=N_WALK_FORWARD_WINDOWS,
        )

        for strategy_class in strategy_classes:
            strategy_name = strategy_class.STRATEGY_NAME
            log.info("optimizer.strategy.start", strategy=strategy_name)

            best = await self._evaluate_all_combos(strategy_class, windows, candles_by_tf)

            if best is None:
                log.warning(
                    "optimizer.strategy.no_passing_combo",
                    strategy=strategy_name,
                )
                # Retain previous active params — no deactivation
                continue

            oos_pnl = best.get("all_oos_pnl", [])
            if len(oos_pnl) < MIN_TRADES_FOR_EVALUATION:
                log.warning(
                    "optimizer.strategy.mc_skipped_too_few_trades",
                    strategy=strategy_name,
                    trade_count=len(oos_pnl),
                )
                continue

            mc_results = run_monte_carlo(
                daily_pnl=np.array(oos_pnl, dtype=float),
                n_simulations=1000,
            )

            if not monte_carlo_passes(mc_results):
                log.warning(
                    "optimizer.strategy.mc_gate_failed",
                    strategy=strategy_name,
                    p95_dd=mc_results["p95_drawdown"],
                    p5_pf=mc_results["p5_profit_factor"],
                    hist_dd=mc_results["historical_max_drawdown"],
                )
                continue  # Retain previous params

            await self._activate_best_params(strategy_name, best)

        log.info("optimizer.run.complete")
