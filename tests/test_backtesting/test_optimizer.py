"""Unit tests for WalkForwardOptimizer — LHS sampling and data guard behaviour."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import DateTime

from src.backtesting.optimizer import (
    BACKTEST_WINDOW_CANDLES,
    SimulatedTradeResult,
    WalkForwardOptimizer,
    _build_daily_pnl_series,
    _compute_aggregate_scores,
    _strategy_lhs_seed,
)
from src.backtesting.regime_detector import RegimeDetector
from src.backtesting.walk_forward import (
    MIN_CANDLES_FOR_OPTIMIZER,
    TradeOutcome,
    WalkForwardWindow,
)
from src.models.signal_data import CandidateSignal, Direction, StrategyName, Timeframe
from src.backtesting.monte_carlo import run_monte_carlo
from src.models.optimizer_result import OptimizerResultORM
from src.risk.events import PositionSizing
from tests.test_backtesting.conftest import make_candle

SAMPLE_PARAM_RANGES: dict[str, tuple[float, float]] = {
    "sweep_atr_mult": (0.2, 0.8),
    "sl_atr_mult": (0.3, 1.0),
    "tp_risk_mult": (1.2, 3.0),
}


class _PatchedSimulationResult:
    def __init__(
        self,
        outcome: TradeOutcome,
        pnl: float,
        pnl_usd: Decimal,
        closed_at: datetime | None,
    ):
        self.outcome = outcome
        self.pnl = pnl
        self.pnl_pct = Decimal(str(pnl)).quantize(Decimal("0.00001"))
        self.pnl_usd = pnl_usd
        self.closed_at = closed_at

    def __iter__(self):
        yield self.outcome
        yield self.pnl


def _sufficient_candles_with_source(
    *,
    source_kind: str | None,
    research_source: str | None = None,
) -> dict[str, list]:
    """Build enough candle mocks for optimizer source-gate tests."""
    ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
    candles_by_tf: dict[str, list] = {}
    for tf, minimum in MIN_CANDLES_FOR_OPTIMIZER.items():
        candle = make_candle(ts, close=100.0)
        if source_kind is not None:
            candle.source_kind = source_kind
        if research_source is not None:
            candle.research_source = research_source
        candles_by_tf[tf] = [candle] * minimum
    return candles_by_tf


# ---------------------------------------------------------------------------
# LHS sampling
# ---------------------------------------------------------------------------


def test_lhs_produces_100_combos():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=100)
    assert len(combos) == 100


def test_lhs_keys_match_param_ranges():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10)
    for combo in combos:
        assert set(combo.keys()) == set(SAMPLE_PARAM_RANGES.keys())


def test_lhs_values_in_range():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=100)
    for combo in combos:
        for name, value in combo.items():
            lo, hi = SAMPLE_PARAM_RANGES[name]
            assert lo <= value <= hi, f"{name}={value} outside [{lo}, {hi}]"


def test_lhs_values_are_python_floats():
    opt = WalkForwardOptimizer()
    combos = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10)
    for combo in combos:
        for value in combo.values():
            assert isinstance(value, float), f"Expected float, got {type(value)}"


def test_lhs_is_deterministic_when_seeded():
    opt = WalkForwardOptimizer()
    seed = 12345
    combos_a = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10, seed=seed)
    combos_b = opt._sample_param_combinations(SAMPLE_PARAM_RANGES, n_combos=10, seed=seed)
    assert combos_a == combos_b


def test_strategy_lhs_seed_is_stable_and_distinct():
    liquidity_seed = _strategy_lhs_seed("liquidity_sweep")
    trend_seed = _strategy_lhs_seed("trend_continuation")
    assert liquidity_seed == _strategy_lhs_seed("liquidity_sweep")
    assert liquidity_seed != trend_seed


def test_optimizer_result_datetime_columns_are_timezone_aware():
    """Optimizer result timestamps must match TIMESTAMPTZ schema in 0001 migration."""
    for column_name in ["train_start", "train_end", "test_start", "test_end", "created_at"]:
        column = OptimizerResultORM.__table__.c[column_name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True, f"{column_name} must be timezone-aware"


def test_optimizer_result_max_drawdown_column_has_expanded_precision():
    column = OptimizerResultORM.__table__.c["max_drawdown"]
    assert column.type.precision == 12
    assert column.type.scale == 5


def test_compute_aggregate_scores_uses_all_windows():
    """Aggregate WFE must be derived from all IS/OOS trades, not just the last window."""
    is_pf, oos_pf, wfe = _compute_aggregate_scores(
        all_is_pnl=[10.0, 10.0, -5.0, -5.0],
        all_oos_pnl=[8.0, 8.0, -4.0, -4.0],
    )
    assert is_pf == pytest.approx(2.0)
    assert oos_pf == pytest.approx(2.0)
    assert wfe == pytest.approx(1.0)


def test_build_daily_pnl_series_sums_same_day_and_fills_gaps():
    """Monte Carlo input must be a dense daily vector with zero-filled missing days."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, tzinfo=timezone.utc)
    series = _build_daily_pnl_series(
        trade_results=[
            SimulatedTradeResult(signal_timestamp=start + timedelta(hours=1), pnl=10.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(hours=5), pnl=-3.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=2, hours=2), pnl=7.5),
        ],
        start=start,
        end=end,
    )
    assert series.tolist() == [7.0, 0.0, 7.5, 0.0]


def test_build_daily_pnl_series_uses_close_date_when_available():
    """Monte Carlo daily input must realize P&L on close date, not signal date."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 4, tzinfo=timezone.utc)
    series = _build_daily_pnl_series(
        trade_results=[
            SimulatedTradeResult(
                signal_timestamp=start + timedelta(hours=1),
                closed_at=start + timedelta(days=1, hours=2),
                pnl=5.0,
            ),
        ],
        start=start,
        end=end,
    )
    assert series.tolist() == [0.0, 5.0, 0.0]


def test_sparse_daily_monte_carlo_pipeline_is_deterministic_and_loss_sensitive():
    """Sparse OOS trades become dense daily PnL without making PF artificial."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=30)
    daily_pnl = _build_daily_pnl_series(
        trade_results=[
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=2), pnl=4.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=10), pnl=-10.0),
            SimulatedTradeResult(signal_timestamp=start + timedelta(days=20), pnl=3.0),
        ],
        start=start,
        end=end,
    )

    first = run_monte_carlo(daily_pnl, n_simulations=500, seed=77)
    second = run_monte_carlo(daily_pnl, n_simulations=500, seed=77)

    assert len(daily_pnl) == 30
    assert np.count_nonzero(daily_pnl) == 3
    assert first == second
    assert first["p5_profit_factor"] == 0.0
    assert first["historical_max_drawdown"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_optimizer_backtest_uses_signal_mode_trade_simulator():
    """Optimizer validation must use the same lifecycle as signal-mode tracking."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_candles = [
        make_candle(start + timedelta(hours=i), close=100.0)
        for i in range(BACKTEST_WINDOW_CANDLES + 73)
    ]
    m15_candles = [
        make_candle(start + timedelta(minutes=15 * i), close=100.0)
        for i in range((BACKTEST_WINDOW_CANDLES + 73) * 4)
    ]
    candles = {
        "H1": h1_candles,
        "M15": m15_candles,
        "H4": h1_candles,
        "D1": h1_candles,
    }
    signal = CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=Direction.BUY,
        entry_price=100.0,
        sl_price=90.0,
        tp1_price=112.0,
        tp2_price=125.0,
        confidence=0.8,
        timeframe=Timeframe.M15,
        params_snapshot={},
    )
    strategy = MagicMock()
    strategy.generate_signals = AsyncMock(return_value=[signal])

    with patch(
        "src.backtesting.optimizer.simulate_signal_mode_trade_outcome",
        return_value=(TradeOutcome.TRAIL, 0.123),
    ) as simulate:
        results = await WalkForwardOptimizer()._run_strategy_backtest_detailed_async(
            strategy,
            candles,
        )

    assert [result.pnl for result in results] == [0.123]
    simulate.assert_called_once()
    assert simulate.call_args.kwargs["equity_at_open"] == Decimal("10000")
    assert simulate.call_args.kwargs["contract_size"] == Decimal("100")
    assert simulate.call_args.kwargs["spread_usd"] == Decimal("0.30")
    assert simulate.call_args.kwargs["slippage_usd"] == Decimal("0.10")
    assert simulate.call_args.kwargs["h1_candles"][-1].timestamp == h1_candles[
        BACKTEST_WINDOW_CANDLES + 72 - 1
    ].timestamp


@pytest.mark.asyncio
async def test_optimizer_backtest_dedups_repeated_signal_windows():
    """Adjacent walk-forward windows must not count the same setup twice."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_candles = [
        make_candle(start + timedelta(hours=i), close=100.0)
        for i in range(BACKTEST_WINDOW_CANDLES + 74)
    ]
    m15_candles = [
        make_candle(start + timedelta(minutes=15 * i), close=100.0)
        for i in range((BACKTEST_WINDOW_CANDLES + 74) * 4)
    ]
    candles = {
        "H1": h1_candles,
        "M15": m15_candles,
        "H4": h1_candles,
        "D1": h1_candles,
    }
    signal = CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=Direction.BUY,
        entry_price=100.0,
        sl_price=90.0,
        tp1_price=112.0,
        tp2_price=125.0,
        confidence=0.8,
        timeframe=Timeframe.M15,
        params_snapshot={},
    )
    strategy = MagicMock()
    strategy.generate_signals = AsyncMock(return_value=[signal])

    with patch(
        "src.backtesting.optimizer.simulate_signal_mode_trade_outcome",
        return_value=(TradeOutcome.TRAIL, 0.123),
    ) as simulate:
        results = await WalkForwardOptimizer()._run_strategy_backtest_detailed_async(
            strategy,
            candles,
        )

    assert len(results) == 1
    simulate.assert_called_once()


@pytest.mark.asyncio
async def test_optimizer_backtest_sizes_against_rolling_equity():
    """Backtest simulation must size each signal with the current rolling equity."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_candles = [
        make_candle(start + timedelta(hours=i), close=100.0)
        for i in range(BACKTEST_WINDOW_CANDLES + 74)
    ]
    m15_candles = [
        make_candle(start + timedelta(minutes=15 * i), close=100.0)
        for i in range((BACKTEST_WINDOW_CANDLES + 74) * 4)
    ]
    candles = {
        "H1": h1_candles,
        "M15": m15_candles,
        "H4": h1_candles,
        "D1": h1_candles,
    }
    first_signal = CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=Direction.BUY,
        entry_price=100.0,
        sl_price=90.0,
        tp1_price=112.0,
        tp2_price=125.0,
        confidence=0.8,
        timeframe=Timeframe.M15,
        params_snapshot={},
    )
    second_signal = first_signal.model_copy(
        update={"entry_price": 130.0, "sl_price": 120.0}
    )
    strategy = MagicMock()
    strategy.generate_signals = AsyncMock(side_effect=[[first_signal], [second_signal]])
    sizing = PositionSizing(
        risk_pct=0.01,
        risk_amount_usd=Decimal("100"),
        size_lots=Decimal("0.10"),
        vol_factor=1.0,
        concentration_reduced=False,
    )
    first_window_end = h1_candles[BACKTEST_WINDOW_CANDLES - 1].timestamp

    with (
        patch(
            "src.backtesting.optimizer.calculate_position_size",
            return_value=sizing,
        ) as size_position,
        patch(
            "src.backtesting.optimizer.simulate_signal_mode_trade_outcome",
            side_effect=[
                _PatchedSimulationResult(
                    TradeOutcome.TP1,
                    0.01,
                    Decimal("100.00"),
                    first_window_end + timedelta(minutes=30),
                ),
                _PatchedSimulationResult(
                    TradeOutcome.SL,
                    -0.02,
                    Decimal("-202.00"),
                    first_window_end + timedelta(hours=2),
                ),
            ],
        ),
    ):
        results = await WalkForwardOptimizer()._run_strategy_backtest_detailed_async(
            strategy,
            candles,
        )

    assert [result.pnl for result in results] == [0.01, -0.02]
    assert [result.pnl_usd for result in results] == [
        Decimal("100.00"),
        Decimal("-202.00"),
    ]
    assert size_position.call_args_list[0].kwargs["equity"] == Decimal("10000")
    assert size_position.call_args_list[1].kwargs["equity"] == Decimal("10100.00")


@pytest.mark.asyncio
async def test_optimizer_backtest_defers_equity_until_trade_closed():
    """Future trade P&L must not resize earlier signals before the close timestamp."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_candles = [
        make_candle(start + timedelta(hours=i), close=100.0)
        for i in range(BACKTEST_WINDOW_CANDLES + 75)
    ]
    m15_candles = [
        make_candle(start + timedelta(minutes=15 * i), close=100.0)
        for i in range((BACKTEST_WINDOW_CANDLES + 75) * 4)
    ]
    candles = {
        "H1": h1_candles,
        "M15": m15_candles,
        "H4": h1_candles,
        "D1": h1_candles,
    }
    first_signal = CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=Direction.BUY,
        entry_price=100.0,
        sl_price=90.0,
        tp1_price=112.0,
        tp2_price=125.0,
        confidence=0.8,
        timeframe=Timeframe.M15,
        params_snapshot={},
    )
    second_signal = first_signal.model_copy(
        update={"entry_price": 130.0, "sl_price": 120.0}
    )
    strategy = MagicMock()
    strategy.generate_signals = AsyncMock(side_effect=[[first_signal], [second_signal]])
    sizing = PositionSizing(
        risk_pct=0.01,
        risk_amount_usd=Decimal("100"),
        size_lots=Decimal("0.10"),
        vol_factor=1.0,
        concentration_reduced=False,
    )
    first_window_end = h1_candles[BACKTEST_WINDOW_CANDLES - 1].timestamp

    with (
        patch(
            "src.backtesting.optimizer.calculate_position_size",
            return_value=sizing,
        ) as size_position,
        patch(
            "src.backtesting.optimizer.simulate_signal_mode_trade_outcome",
            side_effect=[
                _PatchedSimulationResult(
                    TradeOutcome.TP1,
                    0.01,
                    Decimal("100.00"),
                    first_window_end + timedelta(hours=2),
                ),
                _PatchedSimulationResult(
                    TradeOutcome.SL,
                    -0.02,
                    Decimal("-200.00"),
                    first_window_end + timedelta(hours=3),
                ),
            ],
        ),
    ):
        results = await WalkForwardOptimizer()._run_strategy_backtest_detailed_async(
            strategy,
            candles,
        )

    assert [result.pnl_usd for result in results] == [
        Decimal("100.00"),
        Decimal("-200.00"),
    ]
    assert [result.closed_at for result in results] == [
        first_window_end + timedelta(hours=2),
        first_window_end + timedelta(hours=3),
    ]
    assert size_position.call_args_list[0].kwargs["equity"] == Decimal("10000")
    assert size_position.call_args_list[1].kwargs["equity"] == Decimal("10000")
    assert size_position.call_args_list[1].kwargs["same_direction_open_count"] == 1


@pytest.mark.asyncio
async def test_optimizer_backtest_sizes_with_current_h1_atr_context():
    """Sizer must receive ATR value/percentile from H1 candles as of signal time."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    h1_candles = []
    for i in range(BACKTEST_WINDOW_CANDLES + 73):
        ts = start + timedelta(hours=i)
        if i < BACKTEST_WINDOW_CANDLES - 20:
            h1_candles.append(make_candle(ts, close=100.0, high=101.0, low=99.0))
        elif i < BACKTEST_WINDOW_CANDLES:
            h1_candles.append(make_candle(ts, close=100.0, high=112.0, low=88.0))
        else:
            h1_candles.append(make_candle(ts, close=100.0, high=101.0, low=99.0))

    m15_candles = [
        make_candle(start + timedelta(minutes=15 * i), close=100.0)
        for i in range((BACKTEST_WINDOW_CANDLES + 73) * 4)
    ]
    candles = {
        "H1": h1_candles,
        "M15": m15_candles,
        "H4": h1_candles,
        "D1": h1_candles,
    }
    signal = CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=Direction.BUY,
        entry_price=100.0,
        sl_price=90.0,
        tp1_price=112.0,
        tp2_price=125.0,
        confidence=0.8,
        timeframe=Timeframe.M15,
        params_snapshot={},
    )
    strategy = MagicMock()
    strategy.generate_signals = AsyncMock(return_value=[signal])
    sizing = PositionSizing(
        risk_pct=0.01,
        risk_amount_usd=Decimal("100"),
        size_lots=Decimal("0.10"),
        vol_factor=1.0,
        concentration_reduced=False,
    )
    detector = RegimeDetector()
    current_h1_context = h1_candles[:BACKTEST_WINDOW_CANDLES]
    expected_atr = Decimal(str(detector._calculate_atr(current_h1_context)))
    expected_pctile = detector._calculate_atr_percentile(current_h1_context)

    with (
        patch(
            "src.backtesting.optimizer.calculate_position_size",
            return_value=sizing,
        ) as size_position,
        patch(
            "src.backtesting.optimizer.simulate_signal_mode_trade_outcome",
            return_value=(TradeOutcome.TP1, 0.01),
        ),
    ):
        await WalkForwardOptimizer()._run_strategy_backtest_detailed_async(
            strategy,
            candles,
        )

    sizing_kwargs = size_position.call_args.kwargs
    assert sizing_kwargs["atr_value"] == expected_atr
    assert sizing_kwargs["atr_pctile"] == pytest.approx(expected_pctile)
    assert expected_atr > Decimal("0")
    assert expected_pctile != 0.5


# ---------------------------------------------------------------------------
# Data guard — run() must return early when insufficient data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_guard_skips_when_insufficient():
    """run() returns immediately without any DB writes when data is insufficient."""
    opt = WalkForwardOptimizer()

    # Sparse candles — well below minimums
    sparse = {tf: [] for tf in ["M15", "H1", "H4", "D1"]}

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=sparse)),
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as mock_activate,
    ):
        await opt.run()

    mock_activate.assert_not_called()


# ---------------------------------------------------------------------------
# Retain-previous behaviour — no deactivation when no combo passes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retains_previous_on_no_passing_combo():
    """When _evaluate_all_combos returns None, _activate_best_params is not called."""
    opt = WalkForwardOptimizer()

    sufficient = _sufficient_candles_with_source(
        source_kind="research",
        research_source="dukascopy",
    )
    # Give each mock candle a timestamp so max() and window slicing work
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for tf_candles in sufficient.values():
        for c in tf_candles:
            c.timestamp = ts

    evaluate = AsyncMock(return_value=None)
    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=sufficient)),
        patch.object(opt, "_evaluate_all_combos", new=evaluate),
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as mock_activate,
    ):
        await opt.run()

    assert evaluate.await_count == 4
    mock_activate.assert_not_called()
    assert (
        opt.last_run_diagnostics["liquidity_sweep"]["final_stage"]
        == "no_passing_combo"
    )


@pytest.mark.asyncio
async def test_optimizer_diagnostics_include_dukascopy_research_source():
    """Optimizer diagnostics expose Dukascopy when candles carry research markers."""
    opt = WalkForwardOptimizer()
    candles = _sufficient_candles_with_source(
        source_kind="research",
        research_source="dukascopy",
    )

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=candles)),
        patch.object(opt, "_evaluate_all_combos", new=AsyncMock(return_value=None)),
        patch.object(opt, "_activate_best_params", new=AsyncMock()),
    ):
        await opt.run()

    assert opt.last_run_diagnostics["_data_source"]["source_kind"] == "research"
    assert opt.last_run_diagnostics["_data_source"]["research_source"] == "dukascopy"
    assert opt.last_run_diagnostics["liquidity_sweep"]["research_source"] == "dukascopy"


@pytest.mark.asyncio
async def test_optimizer_blocks_activation_without_research_source_marker():
    """Optimizer must not evaluate or activate when source cannot be proven research-grade."""
    opt = WalkForwardOptimizer()
    candles = _sufficient_candles_with_source(source_kind=None)

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=candles)),
        patch.object(opt, "_evaluate_all_combos", new=AsyncMock()) as evaluate,
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as activate,
    ):
        await opt.run()

    evaluate.assert_not_called()
    activate.assert_not_called()
    assert opt.last_run_diagnostics["_data_source"]["source_gate_passed"] is False
    assert opt.last_run_diagnostics["_data_source"]["reason"] == "missing_source_kind"


@pytest.mark.asyncio
async def test_optimizer_blocks_activation_for_runtime_proxy_source():
    """Runtime proxy candles are valid plumbing but not optimizer activation evidence."""
    opt = WalkForwardOptimizer()
    candles = _sufficient_candles_with_source(source_kind="runtime_proxy")

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=candles)),
        patch.object(opt, "_evaluate_all_combos", new=AsyncMock()) as evaluate,
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as activate,
    ):
        await opt.run()

    evaluate.assert_not_called()
    activate.assert_not_called()
    assert opt.last_run_diagnostics["_data_source"]["source_gate_passed"] is False
    assert opt.last_run_diagnostics["_data_source"]["source_kind"] == "runtime_proxy"
    assert opt.last_run_diagnostics["_data_source"]["reason"] == "runtime_proxy_not_research"


@pytest.mark.asyncio
async def test_optimizer_blocks_activation_for_missing_research_source():
    """Research candles without the specific source are not enough for activation."""
    opt = WalkForwardOptimizer()
    candles = _sufficient_candles_with_source(source_kind="research")

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=candles)),
        patch.object(opt, "_evaluate_all_combos", new=AsyncMock()) as evaluate,
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as activate,
    ):
        await opt.run()

    evaluate.assert_not_called()
    activate.assert_not_called()
    assert opt.last_run_diagnostics["_data_source"]["source_gate_passed"] is False
    assert opt.last_run_diagnostics["_data_source"]["source_kind"] == "research"
    assert opt.last_run_diagnostics["_data_source"]["reason"] == "missing_research_source"


@pytest.mark.asyncio
async def test_optimizer_blocks_activation_for_mixed_source_history():
    """A mixed research/runtime history must fail the optimizer source gate."""
    opt = WalkForwardOptimizer()
    candles = _sufficient_candles_with_source(
        source_kind="research",
        research_source="dukascopy",
    )
    candles["H1"][0].source_kind = "runtime_proxy"
    candles["H1"][0].research_source = None

    with (
        patch.object(opt, "_fetch_all_candles", new=AsyncMock(return_value=candles)),
        patch.object(opt, "_evaluate_all_combos", new=AsyncMock()) as evaluate,
        patch.object(opt, "_activate_best_params", new=AsyncMock()) as activate,
    ):
        await opt.run()

    evaluate.assert_not_called()
    activate.assert_not_called()
    assert opt.last_run_diagnostics["_data_source"]["source_gate_passed"] is False
    assert opt.last_run_diagnostics["_data_source"]["source_kind"] == "mixed"
    assert opt.last_run_diagnostics["_data_source"]["reason"] == "runtime_proxy_not_research"


@pytest.mark.asyncio
async def test_evaluate_all_combos_rejects_non_robust_combo():
    """A combo that passes WFE but has fewer than 3 profitable neighbors is rejected."""
    class NonRobustStrategy:
        STRATEGY_NAME = "liquidity_sweep"
        PARAM_RANGES = {
            "a": (0.0, 1.0),
            "b": (0.0, 1.0),
            "c": (0.0, 1.0),
        }

        def __init__(self, params):
            self.params = params

    opt = WalkForwardOptimizer()
    base_combo = {"a": 0.5, "b": 0.5, "c": 0.5}
    windows = [
        WalkForwardWindow(
            window_num=i + 1,
            train_start=datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
            train_end=datetime(2026, 1, 2 + i, tzinfo=timezone.utc),
            oos_start=datetime(2026, 1, 2 + i, tzinfo=timezone.utc),
            oos_end=datetime(2026, 1, 3 + i, tzinfo=timezone.utc),
        )
        for i in range(3)
    ]
    candles_by_tf = {tf: [] for tf in ["M15", "H1", "H4", "D1"]}
    profitable_neighbors = {
        (("a", 0.4), ("b", 0.5), ("c", 0.5)),
        (("a", 0.6), ("b", 0.5), ("c", 0.5)),
    }

    async def fake_backtest(strategy, _slice):
        key = tuple(
            sorted((name, round(value, 3)) for name, value in strategy.params.items())
        )
        if strategy.params == base_combo or key in profitable_neighbors:
            pnl = [0.02, 0.02, 0.02, -0.01, -0.01, -0.01]
        else:
            pnl = [0.005, 0.005, 0.005, -0.01, -0.01, -0.01]
        return [
            SimulatedTradeResult(
                signal_timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc),
                pnl=value,
            )
            for value in pnl
        ]

    with (
        patch.object(opt, "_sample_param_combinations", return_value=[base_combo]),
        patch.object(opt, "_run_strategy_backtest_detailed_async", side_effect=fake_backtest),
    ):
        result = await opt._evaluate_all_combos(NonRobustStrategy, windows, candles_by_tf)

    assert result is None
    diagnostics = opt.last_run_diagnostics["liquidity_sweep"]
    assert diagnostics["best_passing_combo"]["robustness_passed"] is False
    assert diagnostics["best_passing_combo"]["profitable_neighbors"] == 2
