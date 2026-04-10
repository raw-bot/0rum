"""Unit tests for src/pipeline/quota.py — MAX_SIGNALS_PER_DAY quota gate."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    StrategyName,
    Timeframe,
)
from src.pipeline.quota import apply_quota


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


@pytest.mark.asyncio
async def test_quota_all_pass_when_slots_available():
    """0 existing approvals today + 3 ranked signals + max_per_day=5 → all 3 approved."""
    sigs = [(make_signal(), 0.8), (make_signal(), 0.7), (make_signal(), 0.6)]
    with patch("src.pipeline.quota._count_today_approvals", AsyncMock(return_value=0)):
        approved, rejected = await apply_quota(sigs, max_per_day=5)
    assert len(approved) == 3
    assert len(rejected) == 0


@pytest.mark.asyncio
async def test_quota_blocks_when_slots_partially_exhausted():
    """4 existing approvals today + 3 ranked signals + max_per_day=5 → 1 approved, 2 rejected."""
    sigs = [(make_signal(), 0.8), (make_signal(), 0.7), (make_signal(), 0.6)]
    with patch("src.pipeline.quota._count_today_approvals", AsyncMock(return_value=4)):
        approved, rejected = await apply_quota(sigs, max_per_day=5)
    assert len(approved) == 1
    assert len(rejected) == 2


@pytest.mark.asyncio
async def test_quota_fully_exhausted():
    """5 existing approvals today + 2 ranked signals → 0 approved, 2 rejected."""
    sigs = [(make_signal(), 0.8), (make_signal(), 0.7)]
    with patch("src.pipeline.quota._count_today_approvals", AsyncMock(return_value=5)):
        approved, rejected = await apply_quota(sigs, max_per_day=5)
    assert len(approved) == 0
    assert len(rejected) == 2


@pytest.mark.asyncio
async def test_quota_empty_ranked_signals():
    """Empty ranked_signals → returns ([], []) without DB call."""
    with patch("src.pipeline.quota._count_today_approvals", AsyncMock(return_value=0)):
        approved, rejected = await apply_quota([], max_per_day=5)
    assert approved == []
    assert rejected == []
