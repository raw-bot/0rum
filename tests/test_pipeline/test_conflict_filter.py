"""Unit tests for src/pipeline/conflict_filter.py — BUY/SELL conflict resolution."""

from src.models.signal_data import (
    CandidateSignal,
    Direction,
    StrategyName,
    Timeframe,
)
from src.pipeline.conflict_filter import filter_conflicts


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


def test_conflict_higher_confidence_buy_wins():
    """BUY with higher confidence wins over SELL."""
    buy = make_signal(direction=Direction.BUY, confidence=0.8)
    sell = make_signal(direction=Direction.SELL, confidence=0.6)
    kept, rejected = filter_conflicts([buy, sell])
    assert buy in kept
    assert sell in rejected
    assert len(kept) == 1
    assert len(rejected) == 1


def test_conflict_higher_confidence_sell_wins():
    """SELL with higher confidence wins over BUY."""
    buy = make_signal(direction=Direction.BUY, confidence=0.5)
    sell = make_signal(direction=Direction.SELL, confidence=0.9)
    kept, rejected = filter_conflicts([buy, sell])
    assert sell in kept
    assert buy in rejected


def test_conflict_only_buys_all_kept():
    """No conflict when all signals are in the same direction — all kept."""
    sigs = [
        make_signal(direction=Direction.BUY, confidence=0.7),
        make_signal(direction=Direction.BUY, confidence=0.6),
    ]
    kept, rejected = filter_conflicts(sigs)
    assert len(kept) == 2
    assert rejected == []


def test_conflict_empty_input():
    """Empty input returns two empty lists."""
    kept, rejected = filter_conflicts([])
    assert kept == []
    assert rejected == []


def test_conflict_tie_buy_preferred():
    """Equal confidence → BUY wins (>= tie-breaking rule in filter_conflicts)."""
    buy = make_signal(direction=Direction.BUY, confidence=0.7)
    sell = make_signal(direction=Direction.SELL, confidence=0.7)
    kept, rejected = filter_conflicts([buy, sell])
    assert buy in kept
    assert sell in rejected
