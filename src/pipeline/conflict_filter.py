"""Conflict filter for the 0rum signal pipeline.

Resolves opposing BUY/SELL signals for the same instrument (XAUUSD) per CLAUDE.md §10.2.
When both directions are present, the higher-confidence signal wins; the other is rejected.
Tie-breaking uses >= so a deterministic winner is always selected (T-04-04 mitigation).

No DB I/O — pure in-memory function. Callers track status externally.
"""

import structlog

from src.models.signal_data import CandidateSignal, Direction

log = structlog.get_logger(__name__)


def filter_conflicts(
    signals: list[CandidateSignal],
) -> tuple[list[CandidateSignal], list[CandidateSignal]]:
    """Resolve conflicting BUY and SELL signals for the same instrument.

    Since 0rum trades XAUUSD exclusively (single instrument), any simultaneous
    BUY + SELL combination is a conflict. The higher-confidence direction wins;
    all signals from the losing direction are rejected.

    Tie-breaking: when confidence is equal, BUY wins (>= comparison on BUY side).
    This ensures a deterministic, auditable outcome — rejected_count is always logged.

    Input objects are never mutated — callers track status externally.

    Args:
        signals: List of CandidateSignal objects that passed dedup.

    Returns:
        Tuple of (kept, rejected). If no conflict exists (all BUY or all SELL),
        all signals are kept and rejected is empty.
    """
    buys = [s for s in signals if s.direction == Direction.BUY]
    sells = [s for s in signals if s.direction == Direction.SELL]

    # No conflict — all signals are in the same direction
    if not buys or not sells:
        return list(signals), []

    # Conflict exists: pick the highest-confidence representative from each side
    best_buy = max(buys, key=lambda s: s.confidence)
    best_sell = max(sells, key=lambda s: s.confidence)

    # Winner = BUY if tied (>= comparison on BUY) — T-04-04 deterministic tie-break
    if best_buy.confidence >= best_sell.confidence:
        winner = best_buy
        kept = buys
        rejected = sells
    else:
        winner = best_sell
        kept = sells
        rejected = buys

    log.info(
        "pipeline.conflict_resolved",
        winner_direction=winner.direction.value,
        winner_confidence=winner.confidence,
        rejected_count=len(rejected),
    )

    return kept, rejected
