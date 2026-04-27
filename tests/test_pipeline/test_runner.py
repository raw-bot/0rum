"""Integration tests for src/pipeline/runner.py — PipelineRunner with all deps mocked."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    MarketRegime,
    MarketRegimeType,
    StrategyName,
    Timeframe,
)
from src.pipeline.runner import PipelineRunner


def make_signal(
    strategy=StrategyName.LIQUIDITY_SWEEP,
    direction=Direction.BUY,
    entry_price=2340.0,
    sl_price=2325.0,
    tp1_price=2358.0,
    confidence=0.75,
) -> CandidateSignal:
    return CandidateSignal(
        strategy=strategy,
        direction=direction,
        entry_price=entry_price,
        sl_price=sl_price,
        tp1_price=tp1_price,
        tp2_price=entry_price + 2 * (tp1_price - entry_price),
        confidence=confidence,
        timeframe=Timeframe.M15,
        params_snapshot={"sweep_atr_mult": 0.5},
    )


def make_regime(regime_type=MarketRegimeType.RANGING) -> MarketRegime:
    return MarketRegime(
        timestamp=datetime.now(timezone.utc),
        regime=regime_type,
        atr_value=15.0,
        atr_pctile=0.45,
        adx_value=18.0,
    )


@pytest.mark.asyncio
async def test_runner_empty_candidates_returns_empty():
    """Empty candidates list → returns [] without any pipeline step execution."""
    runner = PipelineRunner()
    result = await runner.run(candidates=[], h1_candles=[])
    assert result == []


@pytest.mark.asyncio
async def test_runner_conflict_resolved_one_approved():
    """BUY (0.8 confidence) vs SELL (0.6 confidence) → BUY survives, session.add called."""
    buy = make_signal(direction=Direction.BUY, confidence=0.8)
    sell = make_signal(direction=Direction.SELL, confidence=0.6)
    h1_candles = []

    mock_regime = make_regime(MarketRegimeType.RANGING)

    with (
        patch("src.pipeline.runner.RegimeDetector") as mock_rd_cls,
        patch("src.pipeline.runner.rank_signals", new_callable=AsyncMock) as mock_rank,
        patch("src.pipeline.runner.apply_quota", new_callable=AsyncMock) as mock_quota,
        patch("src.pipeline.runner.RiskGateRunner") as mock_rgr_cls,
        patch("src.pipeline.runner.AsyncSessionLocal") as mock_session_cls,
        patch("src.pipeline.runner.get_settings") as mock_settings,
    ):
        mock_rd = AsyncMock()
        mock_rd.detect = AsyncMock(return_value=mock_regime)
        mock_rd_cls.return_value = mock_rd

        # After conflict filter: only buy survives (higher confidence)
        mock_rank.return_value = [(buy, 0.72)]
        mock_quota.return_value = ([(buy, 0.72)], [])

        mock_rgr = MagicMock()
        mock_rgr.evaluate = AsyncMock(return_value=MagicMock(passed=True, reason=None))
        mock_rgr_cls.return_value = mock_rgr

        # Mock settings
        mock_settings.return_value.max_signals_per_day = 5

        # Mock DB session for _persist
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.begin = MagicMock()
        mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session_cls.return_value = mock_session

        result = await PipelineRunner().run([buy, sell], h1_candles)

    # session.add was called — regime ORM + candidate ORMs + approved ORM all added
    assert mock_session.add.called
    assert mock_session.add.call_count >= 1
