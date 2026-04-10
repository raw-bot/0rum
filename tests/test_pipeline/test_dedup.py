"""Unit tests for src/pipeline/dedup.py — signal deduplication logic."""

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    StrategyName,
    Timeframe,
)
from src.pipeline.dedup import dedup_signals


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


def test_dedup_same_strategy_direction_removes_earlier():
    """Identical strategy+direction+close entry price → earlier signal deduped, later survives."""
    sig1 = make_signal(entry_price=2340.0)
    sig2 = make_signal(entry_price=2340.5)  # within 0.1% of 2340.0 (0.021%)
    survivors, deduped = dedup_signals([sig1, sig2])
    assert sig2 in survivors
    assert sig1 in deduped
    assert len(survivors) == 1
    assert len(deduped) == 1


def test_dedup_different_direction_not_deduped():
    """BUY and SELL with same strategy and close price are NOT duplicates."""
    sig_buy = make_signal(direction=Direction.BUY, entry_price=2340.0)
    sig_sell = make_signal(direction=Direction.SELL, entry_price=2340.0)
    survivors, deduped = dedup_signals([sig_buy, sig_sell])
    assert len(survivors) == 2
    assert len(deduped) == 0


def test_dedup_entry_beyond_01pct_not_deduped():
    """Entry price difference > 0.1% means signals are NOT duplicates."""
    sig1 = make_signal(entry_price=2340.0)
    sig2 = make_signal(entry_price=2345.0)  # 0.21% difference — distinct signal
    survivors, deduped = dedup_signals([sig1, sig2])
    assert len(survivors) == 2
    assert len(deduped) == 0


def test_dedup_empty_input():
    """Empty input list returns two empty lists."""
    survivors, deduped = dedup_signals([])
    assert survivors == []
    assert deduped == []


def test_dedup_different_strategy_not_deduped():
    """Same direction + close price but different strategies are NOT duplicates."""
    sig1 = make_signal(strategy=StrategyName.LIQUIDITY_SWEEP, entry_price=2340.0)
    sig2 = make_signal(strategy=StrategyName.EMA_MOMENTUM, entry_price=2340.5)
    survivors, deduped = dedup_signals([sig1, sig2])
    assert len(survivors) == 2
    assert len(deduped) == 0
