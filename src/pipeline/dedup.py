"""Signal deduplication filter for the 0rum pipeline.

Removes duplicate signals within a 60-minute cooldown window per CLAUDE.md §10.1.
Duplicate criterion: same strategy + same direction + entry price within ±0.1%.
The most-recent signal (later in the input list) survives; earlier duplicates are returned
in the deduped list.

No DB I/O — pure in-memory function. Callers track status externally.
"""

import structlog

from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)


def dedup_signals(
    signals: list[CandidateSignal],
    cooldown_minutes: int = 60,
) -> tuple[list[CandidateSignal], list[CandidateSignal]]:
    """Remove duplicate signals within the cooldown window.

    Two signals are duplicates if they share the same strategy + direction AND
    their entry prices are within ±0.1% of each other. Among duplicates, the
    most-recent signal (later index in `signals`) survives; earlier ones are deduped.

    Since all signals passed in are from the same pipeline run (within the 15-min
    scheduler tick), the full input list is treated as being within the cooldown
    window. Future phases can pre-filter by timestamp if needed.

    Input objects are never mutated — callers track status externally.

    Args:
        signals: List of CandidateSignal objects from the current pipeline run.
        cooldown_minutes: Cooldown window in minutes (default 60). Informational
            for this implementation — signals passed in are assumed to be within
            the window.

    Returns:
        Tuple of (survivors, deduped) where survivors passed dedup and deduped
        are the signals that were eliminated as duplicates.
    """
    # survivors_map: group_key → index in survivors list of current best survivor
    survivors: list[CandidateSignal] = []
    deduped: list[CandidateSignal] = []

    # group_key → index in survivors list
    seen: dict[str, int] = {}

    for signal in signals:
        group_key = f"{signal.strategy.value}:{signal.direction.value}"

        # Check if we already have a survivor for this group key
        if group_key in seen:
            existing_idx = seen[group_key]
            existing = survivors[existing_idx]

            # Check entry price proximity: duplicate if within ±0.1%
            if existing.entry_price > 0 and abs(signal.entry_price - existing.entry_price) / existing.entry_price < 0.001:
                # This signal is a duplicate — most-recent wins, so evict existing
                deduped.append(existing)
                survivors[existing_idx] = signal
                # seen[group_key] remains pointing to existing_idx (now updated)
                log.info(
                    "pipeline.dedup",
                    strategy=signal.strategy.value,
                    direction=signal.direction.value,
                    entry=signal.entry_price,
                )
                continue

        # No duplicate found — add as new survivor
        seen[group_key] = len(survivors)
        survivors.append(signal)

    return survivors, deduped
