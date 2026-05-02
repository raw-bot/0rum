"""Integration tests for the new step 5.5 in src/pipeline/runner.py.

RiskGateRunner is inserted between apply_quota and _persist. Mocks all DB sessions and
the RiskGateRunner class. Verifies that risk-rejected candidates produce REJECTED
CandidateSignalORM rows but NO ApprovedSignalORM rows.
"""

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
from decimal import Decimal

from src.pipeline.runner import PipelineRunner
from src.risk.events import PositionSizing, RiskDecision


def make_signal(direction=Direction.BUY, confidence=0.75) -> CandidateSignal:
    return CandidateSignal(
        strategy=StrategyName.LIQUIDITY_SWEEP,
        direction=direction,
        entry_price=2340.0,
        sl_price=2325.0,
        tp1_price=2370.0,
        confidence=confidence,
        timeframe=Timeframe.M15,
        params_snapshot={"sweep_atr_mult": 0.5},
    )


def make_regime() -> MarketRegime:
    return MarketRegime(
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegimeType.RANGING,
        atr_value=15.0,
        atr_pctile=0.45,
        adx_value=18.0,
    )


def _mock_db_session():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock()
    session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_risk_rejection_no_approved_orm():
    """Risk-rejected candidates produce CandidateSignalORM(status=REJECTED) but no ApprovedSignalORM."""
    buy = make_signal(direction=Direction.BUY, confidence=0.8)
    mock_regime = make_regime()
    mock_session = _mock_db_session()

    with (
        patch("src.pipeline.runner.RegimeDetector") as mock_rd_cls,
        patch("src.pipeline.runner.rank_signals", new_callable=AsyncMock) as mock_rank,
        patch("src.pipeline.runner.apply_quota", new_callable=AsyncMock) as mock_quota,
        patch("src.pipeline.runner.RiskGateRunner") as mock_rgr_cls,
        patch("src.pipeline.runner.AsyncSessionLocal", return_value=mock_session),
        patch("src.pipeline.runner.get_settings") as mock_settings,
    ):
        mock_rd = AsyncMock()
        mock_rd.detect = AsyncMock(return_value=mock_regime)
        mock_rd_cls.return_value = mock_rd

        mock_rank.return_value = [(buy, 0.72)]
        mock_quota.return_value = ([(buy, 0.72)], [])
        mock_settings.return_value.max_signals_per_day = 5

        mock_rgr = MagicMock()
        mock_rgr.evaluate = AsyncMock(
            return_value=RiskDecision(passed=False, reason="daily_loss_limit")
        )
        mock_rgr_cls.return_value = mock_rgr

        result = await PipelineRunner().run([buy], h1_candles=[])

    # Risk-rejected: no ApprovedSignalORM created, result is empty.
    assert result == []
    # session.add was called for regime ORM + candidate ORM — verify candidate added
    assert mock_session.add.called
    # Collect all positional args passed to session.add
    added_types = [type(c.args[0]).__name__ for c in mock_session.add.call_args_list]
    assert "CandidateSignalORM" in added_types
    assert "ApprovedSignalORM" not in added_types


@pytest.mark.asyncio
async def test_risk_acceptance_path_creates_approved_orm():
    """When RiskGateRunner passes, ApprovedSignalORM is created."""
    buy = make_signal(direction=Direction.BUY, confidence=0.8)
    mock_regime = make_regime()
    mock_session = _mock_db_session()

    with (
        patch("src.pipeline.runner.RegimeDetector") as mock_rd_cls,
        patch("src.pipeline.runner.rank_signals", new_callable=AsyncMock) as mock_rank,
        patch("src.pipeline.runner.apply_quota", new_callable=AsyncMock) as mock_quota,
        patch("src.pipeline.runner.RiskGateRunner") as mock_rgr_cls,
        patch("src.pipeline.runner.AsyncSessionLocal", return_value=mock_session),
        patch("src.pipeline.runner.get_settings") as mock_settings,
    ):
        mock_rd = AsyncMock()
        mock_rd.detect = AsyncMock(return_value=mock_regime)
        mock_rd_cls.return_value = mock_rd

        mock_rank.return_value = [(buy, 0.72)]
        mock_quota.return_value = ([(buy, 0.72)], [])
        mock_settings.return_value.max_signals_per_day = 5

        mock_rgr = MagicMock()
        mock_rgr.evaluate = AsyncMock(
            return_value=RiskDecision(
                passed=True,
                reason=None,
                concentration_reduced=False,
                sizing=PositionSizing(
                    risk_pct=0.01,
                    risk_amount_usd=Decimal("100.00"),
                    size_lots=Decimal("0.10"),
                    vol_factor=1.0,
                    concentration_reduced=False,
                ),
            )
        )
        mock_rgr_cls.return_value = mock_rgr

        await PipelineRunner().run([buy], h1_candles=[])

    # Acceptance path: ApprovedSignalORM is created alongside CandidateSignalORM.
    assert mock_session.add.called
    added_types = [type(c.args[0]).__name__ for c in mock_session.add.call_args_list]
    assert "ApprovedSignalORM" in added_types
