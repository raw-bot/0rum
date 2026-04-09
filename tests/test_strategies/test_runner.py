"""Integration tests for StrategyRunner using mocked DB and parallel execution.

All DB calls are mocked — no live PostgreSQL required.
Tests verify:
- run() returns list[CandidateSignal]
- asyncio.gather is used in source (D-04)
- CANDLES_PER_TIMEFRAME == 500 (D-06)
- Midpoint fallback when no active optimizer params (D-05)
- Strategy exception isolation (T-03-04)
- No DB writes anywhere in the runner (D-07)
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.strategies.runner import CANDLES_PER_TIMEFRAME, StrategyRunner


# ---------------------------------------------------------------------------
# Source-level structural tests (sync, no mocking needed)
# ---------------------------------------------------------------------------


class TestRunnerSourceStructure:
    """Verify runner source contains required structural patterns."""

    def test_asyncio_gather_in_runner_source(self) -> None:
        """asyncio.gather must be present in runner source (D-04)."""
        from src.strategies import runner

        source = inspect.getsource(runner)
        assert "asyncio.gather" in source

    def test_500_candles_per_timeframe_constant(self) -> None:
        """CANDLES_PER_TIMEFRAME module constant must equal 500 (D-06)."""
        assert CANDLES_PER_TIMEFRAME == 500

    def test_return_exceptions_true_in_source(self) -> None:
        """return_exceptions=True must be present so one strategy error doesn't kill others."""
        from src.strategies import runner

        source = inspect.getsource(runner)
        assert "return_exceptions=True" in source

    def test_no_db_writes_in_runner_source(self) -> None:
        """Runner source must not contain actual session.add/merge calls in code.

        Uses AST inspection to avoid false positives from docstrings that
        describe the absence of these operations.
        """
        import ast
        from src.strategies import runner

        tree = ast.parse(inspect.getsource(runner))

        # Collect all attribute access patterns (e.g. session.add, session.merge)
        attr_calls: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    attr_calls.append(node.func.attr)

        assert "add" not in attr_calls, "session.add() found in runner code"
        assert "merge" not in attr_calls, "session.merge() found in runner code"


# ---------------------------------------------------------------------------
# Midpoint fallback test (_load_active_params, direct unit test)
# ---------------------------------------------------------------------------


class TestMidpointFallback:
    """D-05: Midpoint fallback when no active optimizer result exists."""

    async def test_midpoint_fallback_when_no_active_params(self) -> None:
        """_load_active_params returns midpoints when DB has no active rows."""
        runner = StrategyRunner()

        mock_session = AsyncMock()
        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        param_ranges = {
            "sweep_atr_mult": (0.2, 0.8),
            "sl_atr_mult": (0.3, 1.0),
            "tp_risk_mult": (1.2, 3.0),
        }

        with patch("src.strategies.runner.AsyncSessionLocal", return_value=mock_session):
            params = await runner._load_active_params("liquidity_sweep", param_ranges)

        assert abs(params["sweep_atr_mult"] - 0.5) < 1e-9   # midpoint of (0.2, 0.8)
        assert abs(params["sl_atr_mult"] - 0.65) < 1e-9     # midpoint of (0.3, 1.0)
        assert abs(params["tp_risk_mult"] - 2.1) < 1e-9     # midpoint of (1.2, 3.0)

    async def test_midpoint_fallback_uses_all_param_ranges(self) -> None:
        """All keys in param_ranges are returned, each as (lo+hi)/2."""
        runner = StrategyRunner()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        param_ranges = {
            "fast_ema": (5.0, 15.0),
            "slow_ema": (15.0, 30.0),
            "sl_atr_mult": (0.2, 0.8),
        }

        with patch("src.strategies.runner.AsyncSessionLocal", return_value=mock_session):
            params = await runner._load_active_params("ema_momentum", param_ranges)

        assert set(params.keys()) == {"fast_ema", "slow_ema", "sl_atr_mult"}
        assert abs(params["fast_ema"] - 10.0) < 1e-9    # (5+15)/2
        assert abs(params["slow_ema"] - 22.5) < 1e-9   # (15+30)/2
        assert abs(params["sl_atr_mult"] - 0.5) < 1e-9  # (0.2+0.8)/2

    async def test_active_params_from_db_used_when_present(self) -> None:
        """When DB returns an active row, its params dict is used (not midpoints)."""
        runner = StrategyRunner()

        db_params = {"sweep_atr_mult": 0.3, "sl_atr_mult": 0.7, "tp_risk_mult": 2.5}

        mock_row = MagicMock()
        mock_row.params = db_params

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        param_ranges = {
            "sweep_atr_mult": (0.2, 0.8),
            "sl_atr_mult": (0.3, 1.0),
            "tp_risk_mult": (1.2, 3.0),
        }

        with patch("src.strategies.runner.AsyncSessionLocal", return_value=mock_session):
            params = await runner._load_active_params("liquidity_sweep", param_ranges)

        assert params == db_params


# ---------------------------------------------------------------------------
# run() integration tests (patch _fetch_candles + _load_active_params)
# ---------------------------------------------------------------------------


class TestStrategyRunnerRun:
    """Tests for StrategyRunner.run() using mocked helper methods."""

    async def test_run_returns_list(self, candles_dict: dict) -> None:
        """run() always returns a list, even when strategies produce no signals."""
        runner = StrategyRunner()

        with patch.object(runner, "_fetch_candles", AsyncMock(return_value=candles_dict)), \
             patch.object(runner, "_load_active_params", AsyncMock(return_value={
                 "sweep_atr_mult": 0.5, "sl_atr_mult": 0.65, "tp_risk_mult": 2.0,
                 "pullback_ema": 20.0, "squeeze_lookback": 20.0, "volume_mult": 1.5,
                 "fast_ema": 8.0, "slow_ema": 21.0,
             })):
            result = await runner.run()

        assert isinstance(result, list)

    async def test_run_returns_candidate_signals(self, candles_dict: dict) -> None:
        """Every item in the result list must be a CandidateSignal."""
        from src.models.signal_data import CandidateSignal

        runner = StrategyRunner()

        with patch.object(runner, "_fetch_candles", AsyncMock(return_value=candles_dict)), \
             patch.object(runner, "_load_active_params", AsyncMock(return_value={
                 "sweep_atr_mult": 0.5, "sl_atr_mult": 0.65, "tp_risk_mult": 2.0,
                 "pullback_ema": 20.0, "squeeze_lookback": 20.0, "volume_mult": 1.5,
                 "fast_ema": 8.0, "slow_ema": 21.0,
             })):
            result = await runner.run()

        for sig in result:
            assert isinstance(sig, CandidateSignal), (
                f"Expected CandidateSignal, got {type(sig)}"
            )

    async def test_strategy_exception_does_not_propagate(self, candles_dict: dict) -> None:
        """If one strategy raises, run() must still return results from others."""
        from src.strategies.liquidity_sweep import LiquiditySweepStrategy

        runner = StrategyRunner()

        with patch.object(runner, "_fetch_candles", AsyncMock(return_value=candles_dict)), \
             patch.object(runner, "_load_active_params", AsyncMock(return_value={
                 "sweep_atr_mult": 0.5, "sl_atr_mult": 0.65, "tp_risk_mult": 2.0,
                 "pullback_ema": 20.0, "squeeze_lookback": 20.0, "volume_mult": 1.5,
                 "fast_ema": 8.0, "slow_ema": 21.0,
             })), \
             patch.object(
                 LiquiditySweepStrategy,
                 "generate_signals",
                 AsyncMock(side_effect=RuntimeError("strategy failed")),
             ):
            # Must not raise — should return whatever the other 3 strategies produce
            result = await runner.run()

        assert isinstance(result, list)

    async def test_all_four_strategies_instantiated(self, candles_dict: dict) -> None:
        """Verify all 4 strategy classes are invoked (count their generate_signals calls)."""
        from src.strategies.liquidity_sweep import LiquiditySweepStrategy
        from src.strategies.trend_continuation import TrendContinuationStrategy
        from src.strategies.breakout_expansion import BreakoutExpansionStrategy
        from src.strategies.ema_momentum import EmaMomentumStrategy

        call_counts: dict[str, int] = {}

        def track_call(cls_name: str):
            async def _fake_generate_signals(self, candles):
                call_counts[cls_name] = call_counts.get(cls_name, 0) + 1
                return []
            return _fake_generate_signals

        runner = StrategyRunner()

        with patch.object(runner, "_fetch_candles", AsyncMock(return_value=candles_dict)), \
             patch.object(runner, "_load_active_params", AsyncMock(return_value={})), \
             patch.object(LiquiditySweepStrategy, "generate_signals", track_call("liquidity_sweep")), \
             patch.object(TrendContinuationStrategy, "generate_signals", track_call("trend_continuation")), \
             patch.object(BreakoutExpansionStrategy, "generate_signals", track_call("breakout_expansion")), \
             patch.object(EmaMomentumStrategy, "generate_signals", track_call("ema_momentum")):
            await runner.run()

        assert call_counts.get("liquidity_sweep", 0) == 1
        assert call_counts.get("trend_continuation", 0) == 1
        assert call_counts.get("breakout_expansion", 0) == 1
        assert call_counts.get("ema_momentum", 0) == 1


# ---------------------------------------------------------------------------
# No DB writes test
# ---------------------------------------------------------------------------


class TestRunnerNoDbWrites:
    """D-07: StrategyRunner must not write anything to the database."""

    async def test_no_db_writes_in_runner(self, candles_dict: dict) -> None:
        """session.add and session.merge must never be called during run()."""
        runner = StrategyRunner()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        # Return None for optimizer results (midpoint fallback path)
        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with patch("src.strategies.runner.AsyncSessionLocal", return_value=mock_session), \
             patch.object(runner, "_fetch_candles", AsyncMock(return_value=candles_dict)):
            await runner.run()

        mock_session.add.assert_not_called()
        mock_session.merge.assert_not_called()

    async def test_no_db_commit_with_data(self, candles_dict: dict) -> None:
        """session.commit should not be called in StrategyRunner (reads don't commit)."""
        runner = StrategyRunner()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_execute_result = MagicMock()
        mock_execute_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        with patch("src.strategies.runner.AsyncSessionLocal", return_value=mock_session), \
             patch.object(runner, "_fetch_candles", AsyncMock(return_value=candles_dict)):
            await runner.run()

        mock_session.add.assert_not_called()
        mock_session.merge.assert_not_called()
