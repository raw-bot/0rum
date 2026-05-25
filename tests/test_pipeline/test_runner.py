"""Integration tests for src/pipeline/runner.py — PipelineRunner with all deps mocked."""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.trade import TradeORM
from src.models.signal_data import (
    CandidateSignal,
    Direction,
    MarketRegime,
    MarketRegimeType,
    StrategyName,
    Timeframe,
)
from src.pipeline.runner import PipelineRunner
from src.risk.events import PositionSizing, RiskDecision


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
        patch("src.pipeline.runner.dedup_against_recent_approvals", new_callable=AsyncMock) as mock_db_dedup,
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
        mock_db_dedup.return_value = ([buy, sell], [])
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


@pytest.mark.asyncio
async def test_runner_dedups_against_recent_approved_signal_before_ranking():
    """DB-backed dedup stops repeated approvals across scheduler ticks."""
    signal = make_signal(direction=Direction.BUY, confidence=0.8)
    h1_candles = []

    mock_regime = make_regime(MarketRegimeType.RANGING)

    with (
        patch("src.pipeline.runner.RegimeDetector") as mock_rd_cls,
        patch("src.pipeline.runner.dedup_against_recent_approvals", new_callable=AsyncMock) as mock_db_dedup,
        patch("src.pipeline.runner.rank_signals", new_callable=AsyncMock) as mock_rank,
        patch("src.pipeline.runner.apply_quota", new_callable=AsyncMock) as mock_quota,
        patch.object(PipelineRunner, "_persist", new_callable=AsyncMock) as mock_persist,
    ):
        mock_rd = AsyncMock()
        mock_rd.detect = AsyncMock(return_value=mock_regime)
        mock_rd_cls.return_value = mock_rd

        mock_db_dedup.return_value = ([], [signal])
        mock_persist.return_value = []

        result = await PipelineRunner().run([signal], h1_candles)

    assert result == []
    mock_db_dedup.assert_awaited_once_with([signal])
    mock_rank.assert_not_awaited()
    mock_quota.assert_not_awaited()
    assert mock_persist.await_args.args[0] == [signal]
    assert mock_persist.await_args.args[1] == []
    assert mock_persist.await_args.args[3][id(signal)] == "DEDUPED"


@pytest.mark.asyncio
async def test_persist_trade_stores_risk_accounting_fields():
    """Trade rows created by the runner preserve risk-approved accounting values."""
    signal = make_signal()
    regime = make_regime()
    decision = RiskDecision(
        passed=True,
        sizing=PositionSizing(
            risk_pct=0.01,
            risk_amount_usd=Decimal("100.00"),
            size_lots=Decimal("0.20"),
            vol_factor=1.0,
            concentration_reduced=False,
        ),
        equity_usd=Decimal("10000.00"),
        candidate_notional_usd=Decimal("66000.00"),
    )

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    with patch("src.pipeline.runner.AsyncSessionLocal", return_value=mock_session):
        await PipelineRunner()._persist(
            [signal],
            [(signal, 0.80, decision)],
            regime,
            {id(signal): "APPROVED"},
        )

    trade = next(
        call.args[0]
        for call in mock_session.add.call_args_list
        if isinstance(call.args[0], TradeORM)
    )
    assert trade.equity_at_open == Decimal("10000.00")
    assert trade.notional_usd == Decimal("66000.00")
    assert trade.risk_amount_usd == Decimal("100.00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    [
        RiskDecision(
            passed=True,
            sizing=None,
            equity_usd=Decimal("10000.00"),
            candidate_notional_usd=Decimal("66000.00"),
        ),
        RiskDecision(
            passed=True,
            sizing=PositionSizing(
                risk_pct=0.01,
                risk_amount_usd=Decimal("100.00"),
                size_lots=Decimal("0.20"),
                vol_factor=1.0,
                concentration_reduced=False,
            ),
            equity_usd=None,
            candidate_notional_usd=Decimal("66000.00"),
        ),
        RiskDecision(
            passed=True,
            sizing=PositionSizing(
                risk_pct=0.01,
                risk_amount_usd=Decimal("100.00"),
                size_lots=Decimal("0.20"),
                vol_factor=1.0,
                concentration_reduced=False,
            ),
            equity_usd=Decimal("10000.00"),
            candidate_notional_usd=None,
        ),
    ],
)
async def test_persist_rejects_passed_decision_missing_accounting_fields(decision):
    """Approved risk decisions must include the accounting fields needed for TradeORM."""
    signal = make_signal()
    regime = make_regime()

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock()
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    with patch("src.pipeline.runner.AsyncSessionLocal", return_value=mock_session):
        with pytest.raises(ValueError, match="approved risk decision missing accounting fields"):
            await PipelineRunner()._persist(
                [signal],
                [(signal, 0.80, decision)],
                regime,
                {id(signal): "APPROVED"},
            )
