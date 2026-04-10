"""Unit tests for src/pipeline/ranker.py — composite scoring and REGIME_ALIGNMENT_MAP."""

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
from src.pipeline.ranker import REGIME_ALIGNMENT_MAP, rank_signals


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
async def test_regime_alignment_map_liquidity_sweep_aligned_with_ranging():
    """liquidity_sweep is aligned with RANGING, not HIGH_VOL."""
    assert "RANGING" in REGIME_ALIGNMENT_MAP["liquidity_sweep"]
    assert "HIGH_VOL" not in REGIME_ALIGNMENT_MAP["liquidity_sweep"]


@pytest.mark.asyncio
async def test_regime_alignment_map_ema_momentum_not_aligned_with_ranging():
    """ema_momentum is NOT aligned with RANGING — only TRENDING_UP/DOWN."""
    assert "RANGING" not in REGIME_ALIGNMENT_MAP["ema_momentum"]
    assert "TRENDING_UP" in REGIME_ALIGNMENT_MAP["ema_momentum"]


@pytest.mark.asyncio
async def test_regime_alignment_map_breakout_includes_high_vol():
    """breakout_expansion is aligned with HIGH_VOL."""
    assert "HIGH_VOL" in REGIME_ALIGNMENT_MAP["breakout_expansion"]


@pytest.mark.asyncio
async def test_rank_signals_sorted_descending():
    """Higher confidence + aligned regime signal ranks above lower confidence + misaligned."""
    sig_high = make_signal(confidence=0.9, strategy=StrategyName.BREAKOUT_EXPANSION)
    sig_low = make_signal(confidence=0.3, strategy=StrategyName.LIQUIDITY_SWEEP)
    regime = make_regime(MarketRegimeType.HIGH_VOL)

    with patch("src.pipeline.ranker.AsyncSessionLocal") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # triggers WFE fallback (sync call)
        mock_session.execute = AsyncMock(return_value=mock_result)

        ranked = await rank_signals([sig_high, sig_low], regime)

    assert len(ranked) == 2
    assert ranked[0][0] == sig_high  # higher score first
    assert ranked[-1][0] == sig_low
    assert ranked[0][1] > ranked[-1][1]


@pytest.mark.asyncio
async def test_wfe_fallback_used_when_no_db_row():
    """When no optimizer_results row found, WFE fallback 0.5 is used — score is non-zero."""
    sig = make_signal()
    regime = make_regime()
    with patch("src.pipeline.ranker.AsyncSessionLocal") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # sync call in _fetch_strategy_wfe
        mock_session.execute = AsyncMock(return_value=mock_result)
        ranked = await rank_signals([sig], regime)
    # fallback_wfe=0.5 — score should be between 0 and 1
    assert len(ranked) == 1
    assert 0 < ranked[0][1] < 1.0


@pytest.mark.asyncio
async def test_rank_signals_empty_returns_empty():
    """Empty signal list returns empty list without DB calls."""
    regime = make_regime()
    ranked = await rank_signals([], regime)
    assert ranked == []
