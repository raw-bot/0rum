"""Walk-forward optimizer — LHS sampling, backtesting, WFE gating, DB writes.

Architecture (per RESEARCH.md Sync/Async Decision):
  async entry (run()) → sync heavy compute (LHS, trade simulation) → async DB write.
  No asyncio.run() anywhere — the APScheduler job is already a coroutine.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
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
LHS_BASE_SEED = 20260425


@dataclass(frozen=True)
class SimulatedTradeResult:
    """One simulated trade outcome tagged with the signal timestamp."""

    signal_timestamp: datetime
    pnl: float


def _compute_aggregate_scores(
    all_is_pnl: list[float],
    all_oos_pnl: list[float],
) -> tuple[float, float, float]:
    """Compute aggregate IS PF, aggregate OOS PF, and aggregate WFE."""
    is_pf = (
        _compute_profit_factor(np.array(all_is_pnl, dtype=float))
        if all_is_pnl
        else 0.0
    )
    oos_pf = (
        _compute_profit_factor(np.array(all_oos_pnl, dtype=float))
        if all_oos_pnl
        else 0.0
    )
    return is_pf, oos_pf, _compute_wfe(is_pf, oos_pf)


def _build_daily_pnl_series(
    trade_results: list[SimulatedTradeResult],
    start: datetime,
    end: datetime,
) -> np.ndarray:
    """Aggregate simulated trade P&L into a dense UTC daily vector.

    The Monte Carlo module is defined in terms of daily P&L, so this helper
    groups all simulated trade outcomes by UTC calendar day and fills missing
    days with 0.0 across the evaluation span.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    else:
        start = start.astimezone(timezone.utc)

    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)

    if end <= start:
        return np.array([], dtype=float)

    daily_totals: dict[date, float] = {}
    for trade in trade_results:
        ts = trade.signal_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        trade_day = ts.date()
        daily_totals[trade_day] = daily_totals.get(trade_day, 0.0) + trade.pnl

    values: list[float] = []
    current_day = start.date()
    end_day = end.date()
    while current_day < end_day:
        values.append(daily_totals.get(current_day, 0.0))
        current_day += timedelta(days=1)

    return np.array(values, dtype=float)


def _extract_diagnostic_snapshot(
    combo: dict[str, float],
    wfe: float,
    is_score: float,
    oos_score: float,
    oos_pf_per_window: list[float],
    train_trade_counts: list[int],
    oos_trade_counts: list[int],
    trade_count: int,
    windows_with_too_few_is_trades: int,
    wfe_minimum: float,
) -> dict[str, Any]:
    """Build a compact per-combo diagnostic snapshot for post-mortem analysis."""
    profitable_windows = sum(1 for pf in oos_pf_per_window if pf > 1.0)
    multi_window_passed = multi_window_gate_passes(oos_pf_per_window)
    return {
        "params": combo,
        "wfe": wfe,
        "is_score": is_score,
        "oos_score": oos_score,
        "oos_pf_per_window": oos_pf_per_window,
        "profitable_windows": profitable_windows,
        "multi_window_passed": multi_window_passed,
        "wfe_passed": wfe >= wfe_minimum,
        "trade_count": trade_count,
        "train_trade_counts": train_trade_counts,
        "oos_trade_counts": oos_trade_counts,
        "too_few_trades": windows_with_too_few_is_trades > 0,
        "windows_with_too_few_is_trades": windows_with_too_few_is_trades,
    }


def _strategy_lhs_seed(strategy_name: str) -> int:
    """Return a stable per-strategy LHS seed.

    Python's built-in hash is process-randomized, so derive the seed from a
    stable digest instead to keep optimizer sampling reproducible across runs.
    """
    digest = sha256(strategy_name.encode("utf-8")).digest()
    return (LHS_BASE_SEED + int.from_bytes(digest[:4], "big")) % (2**32)


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
        self.last_run_diagnostics: dict[str, dict[str, Any]] = {}

    def _sample_param_combinations(
        self,
        param_ranges: dict[str, tuple[float, float]],
        n_combos: int,
        seed: int | None = None,
    ) -> list[dict[str, float]]:
        """Generate n_combos LHS parameter combinations for one strategy.

        Uses LatinHypercube(d=N_PARAMS) — NOT n_components= (deprecated before
        scipy 1.10; d= is correct in scipy 1.17.1). Values are clamped to
        declared bounds to guard against float-precision edge values (ASVS V5).

        Args:
            param_ranges: Strategy PARAM_RANGES dict mapping name → (lo, hi).
            n_combos: Number of combinations (100 per CONTEXT.md locked decision).
            seed: Optional deterministic seed for reproducible LHS sampling.

        Returns:
            List of n_combos dicts, each mapping param_name → clamped float value.
        """
        param_names = list(param_ranges.keys())
        d = len(param_names)
        l_bounds = [param_ranges[k][0] for k in param_names]
        u_bounds = [param_ranges[k][1] for k in param_names]

        sampler = LatinHypercube(d=d, seed=seed)
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
        """Compatibility wrapper returning only float P&L values."""
        trade_results = await self._run_strategy_backtest_detailed_async(
            strategy_instance,
            candle_slice,
        )
        return [result.pnl for result in trade_results]

    async def _run_strategy_backtest_detailed_async(
        self,
        strategy_instance: Any,
        candle_slice: dict[str, list],
    ) -> list[SimulatedTradeResult]:
        """Slide a 500-candle window across candle_slice and collect tagged P&L.

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
            List of simulated trade results tagged with the signal timestamp.
        """
        trade_results: list[SimulatedTradeResult] = []
        h1_candles = candle_slice.get("H1", [])
        n = len(h1_candles)

        if n < BACKTEST_WINDOW_CANDLES + 50:
            return trade_results

        tf_timestamps: dict[str, list] = {
            tf: [c.timestamp for c in all_tf_candles]
            for tf, all_tf_candles in candle_slice.items()
        }

        for start in range(0, n - BACKTEST_WINDOW_CANDLES - 50, BACKTEST_STEP_CANDLES):
            end = start + BACKTEST_WINDOW_CANDLES
            window_end_ts = h1_candles[end - 1].timestamp

            window_candles: dict[str, list] = {}
            for tf, all_tf_candles in candle_slice.items():
                timestamps = tf_timestamps[tf]
                upper_idx = bisect_right(timestamps, window_end_ts)
                lower_idx = max(0, upper_idx - BACKTEST_WINDOW_CANDLES)
                window_candles[tf] = all_tf_candles[lower_idx:upper_idx]

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
                    trade_results.append(
                        SimulatedTradeResult(
                            signal_timestamp=window_end_ts,
                            pnl=pnl,
                        )
                    )
                except Exception as exc:
                    log.debug("optimizer.outcome_sim_error", error=str(exc))

        return trade_results

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
        strategy_name = strategy_class.STRATEGY_NAME
        combos = self._sample_param_combinations(
            param_ranges=strategy_class.PARAM_RANGES,
            n_combos=self._settings.lhs_combos,
            seed=_strategy_lhs_seed(strategy_name),
        )

        best: dict[str, Any] | None = None
        best_wfe: float = -1.0
        diagnostics: dict[str, Any] = {
            "strategy": strategy_name,
            "combos_evaluated": 0,
            "best_combo": None,
            "best_passing_combo": None,
        }
        window_slices: list[tuple[WalkForwardWindow, dict[str, list], dict[str, list]]] = []

        for window in windows:
            is_slice: dict[str, list] = {}
            oos_slice: dict[str, list] = {}
            for tf, cs in candles_by_tf.items():
                timestamps = [c.timestamp for c in cs]
                is_start = bisect_left(timestamps, window.train_start)
                is_end = bisect_left(timestamps, window.train_end)
                oos_start = bisect_left(timestamps, window.oos_start)
                oos_end = bisect_left(timestamps, window.oos_end)
                is_slice[tf] = cs[is_start:is_end]
                oos_slice[tf] = cs[oos_start:oos_end]
            window_slices.append((window, is_slice, oos_slice))

        for combo in combos:
            diagnostics["combos_evaluated"] += 1
            oos_pfs: list[float] = []
            all_is_pnl: list[float] = []
            all_oos_pnl: list[float] = []
            all_oos_trades: list[SimulatedTradeResult] = []
            total_trades: int = 0
            train_trade_counts: list[int] = []
            oos_trade_counts: list[int] = []
            windows_with_too_few_is_trades = 0

            for window, is_slice, oos_slice in window_slices:
                strategy = strategy_class(params=combo)
                strategy.emit_signal_logs = False
                strategy.emit_diagnostic_logs = False

                is_trade_results = await self._run_strategy_backtest_detailed_async(
                    strategy,
                    is_slice,
                )
                oos_trade_results = await self._run_strategy_backtest_detailed_async(
                    strategy,
                    oos_slice,
                )
                is_pnl = [result.pnl for result in is_trade_results]
                oos_pnl = [result.pnl for result in oos_trade_results]
                train_trade_counts.append(len(is_pnl))
                oos_trade_counts.append(len(oos_pnl))

                if len(is_pnl) < MIN_TRADES_FOR_EVALUATION:
                    windows_with_too_few_is_trades += 1
                    oos_pfs.append(0.0)
                    continue

                is_pf = _compute_profit_factor(np.array(is_pnl, dtype=float))
                oos_pf = _compute_profit_factor(np.array(oos_pnl, dtype=float))
                oos_pfs.append(oos_pf)

                all_is_pnl.extend(is_pnl)
                total_trades += len(oos_pnl)
                all_oos_pnl.extend(oos_pnl)
                all_oos_trades.extend(oos_trade_results)

            aggregate_is_pf, aggregate_oos_pf, wfe = _compute_aggregate_scores(
                all_is_pnl=all_is_pnl,
                all_oos_pnl=all_oos_pnl,
            )
            combo_snapshot = _extract_diagnostic_snapshot(
                combo=combo,
                wfe=wfe,
                is_score=aggregate_is_pf,
                oos_score=aggregate_oos_pf,
                oos_pf_per_window=oos_pfs,
                train_trade_counts=train_trade_counts,
                oos_trade_counts=oos_trade_counts,
                trade_count=total_trades,
                windows_with_too_few_is_trades=windows_with_too_few_is_trades,
                wfe_minimum=self._settings.wfe_minimum,
            )

            if (
                diagnostics["best_combo"] is None
                or combo_snapshot["wfe"] > diagnostics["best_combo"]["wfe"]
            ):
                diagnostics["best_combo"] = deepcopy(combo_snapshot)

            if wfe < self._settings.wfe_minimum:
                continue

            if not combo_snapshot["multi_window_passed"]:
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
                    "is_score": aggregate_is_pf,
                    "oos_score": aggregate_oos_pf,
                    "oos_pf_per_window": oos_pfs,
                    "trade_count": total_trades,
                    "win_rate": win_rate,
                    "max_dd": max_dd,
                    "train_start": windows[0].train_start,
                    "train_end": windows[-1].train_end,
                    "test_start": windows[0].oos_start,
                    "test_end": windows[-1].oos_end,
                    "latest_window": windows[-1],
                    "all_oos_pnl": all_oos_pnl,
                    "all_oos_trades": all_oos_trades,
                }
                diagnostics["best_passing_combo"] = deepcopy(combo_snapshot)

        self.last_run_diagnostics[strategy_name] = diagnostics
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
        train_start = result.get("train_start", window.train_start)
        train_end = result.get("train_end", window.train_end)
        test_start = result.get("test_start", window.oos_start)
        test_end = result.get("test_end", window.oos_end)

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
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
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
        self.last_run_diagnostics = {}

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
            diagnostics = self.last_run_diagnostics.setdefault(
                strategy_name,
                {
                    "strategy": strategy_name,
                    "combos_evaluated": 0,
                    "best_combo": None,
                    "best_passing_combo": None,
                },
            )

            if best is None:
                best_combo = diagnostics.get("best_combo") or {}
                diagnostics["final_stage"] = "no_passing_combo"
                log.warning(
                    "optimizer.strategy.no_passing_combo",
                    strategy=strategy_name,
                )
                log.info(
                    "optimizer.strategy.diagnostics",
                    strategy=strategy_name,
                    final_stage="no_passing_combo",
                    combos_evaluated=diagnostics.get("combos_evaluated", 0),
                    best_wfe_seen=best_combo.get("wfe"),
                    best_combo_multi_window_passed=best_combo.get("multi_window_passed"),
                    best_combo_profitable_windows=best_combo.get("profitable_windows"),
                    best_combo_trade_count=best_combo.get("trade_count"),
                    best_combo_train_trade_counts=best_combo.get("train_trade_counts"),
                    best_combo_oos_trade_counts=best_combo.get("oos_trade_counts"),
                    too_few_trades=best_combo.get("too_few_trades"),
                    windows_with_too_few_is_trades=best_combo.get("windows_with_too_few_is_trades"),
                    best_oos_pf_per_window=best_combo.get("oos_pf_per_window"),
                )
                # Retain previous active params — no deactivation
                continue

            oos_pnl = best.get("all_oos_pnl", [])
            diagnostics["best_passing_combo"] = diagnostics.get("best_passing_combo") or {
                "wfe": best.get("best_wfe"),
                "trade_count": len(oos_pnl),
            }
            if len(oos_pnl) < MIN_TRADES_FOR_EVALUATION:
                diagnostics["final_stage"] = "mc_skipped_too_few_trades"
                diagnostics["too_few_trades_for_mc"] = True
                log.warning(
                    "optimizer.strategy.mc_skipped_too_few_trades",
                    strategy=strategy_name,
                    trade_count=len(oos_pnl),
                )
                log.info(
                    "optimizer.strategy.diagnostics",
                    strategy=strategy_name,
                    final_stage="mc_skipped_too_few_trades",
                    combos_evaluated=diagnostics.get("combos_evaluated", 0),
                    best_wfe_seen=diagnostics["best_passing_combo"].get("wfe"),
                    best_combo_trade_count=diagnostics["best_passing_combo"].get("trade_count"),
                    too_few_trades_for_mc=True,
                )
                continue

            latest_window = best.get("latest_window")
            test_start = best.get("test_start")
            test_end = best.get("test_end")
            if latest_window is not None:
                test_start = test_start or latest_window.oos_start
                test_end = test_end or latest_window.oos_end

            mc_results = run_monte_carlo(
                daily_pnl=_build_daily_pnl_series(
                    trade_results=best.get("all_oos_trades", []),
                    start=test_start,
                    end=test_end,
                ),
                n_simulations=1000,
                seed=_strategy_lhs_seed(f"{strategy_name}:monte_carlo"),
            )
            dd_gate_passed = (
                mc_results["p95_drawdown"] <= 2.0 * mc_results["historical_max_drawdown"]
            )
            pf_gate_passed = mc_results["p5_profit_factor"] > 1.0
            diagnostics["monte_carlo"] = {
                **mc_results,
                "dd_gate_passed": dd_gate_passed,
                "pf_gate_passed": pf_gate_passed,
            }

            if not monte_carlo_passes(mc_results):
                diagnostics["final_stage"] = "mc_gate_failed"
                log.warning(
                    "optimizer.strategy.mc_gate_failed",
                    strategy=strategy_name,
                    p95_dd=mc_results["p95_drawdown"],
                    p5_pf=mc_results["p5_profit_factor"],
                    hist_dd=mc_results["historical_max_drawdown"],
                )
                log.info(
                    "optimizer.strategy.diagnostics",
                    strategy=strategy_name,
                    final_stage="mc_gate_failed",
                    combos_evaluated=diagnostics.get("combos_evaluated", 0),
                    best_wfe_seen=diagnostics["best_passing_combo"].get("wfe"),
                    best_combo_trade_count=diagnostics["best_passing_combo"].get("trade_count"),
                    mc_p95_drawdown=mc_results["p95_drawdown"],
                    mc_historical_drawdown=mc_results["historical_max_drawdown"],
                    mc_p5_profit_factor=mc_results["p5_profit_factor"],
                    mc_dd_gate_passed=dd_gate_passed,
                    mc_pf_gate_passed=pf_gate_passed,
                )
                continue  # Retain previous params

            await self._activate_best_params(strategy_name, best)
            diagnostics["final_stage"] = "activated"
            log.info(
                "optimizer.strategy.diagnostics",
                strategy=strategy_name,
                final_stage="activated",
                combos_evaluated=diagnostics.get("combos_evaluated", 0),
                best_wfe_seen=diagnostics["best_passing_combo"].get("wfe"),
                best_combo_trade_count=diagnostics["best_passing_combo"].get("trade_count"),
                mc_p95_drawdown=mc_results["p95_drawdown"],
                mc_historical_drawdown=mc_results["historical_max_drawdown"],
                mc_p5_profit_factor=mc_results["p5_profit_factor"],
                mc_dd_gate_passed=dd_gate_passed,
                mc_pf_gate_passed=pf_gate_passed,
            )

        log.info("optimizer.run.complete")
