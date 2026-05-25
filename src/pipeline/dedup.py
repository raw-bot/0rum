"""Signal deduplication filter for the 0rum pipeline.

Removes duplicate signals within a 60-minute cooldown window per CLAUDE.md §10.1.
Duplicate criterion: same strategy + same direction + entry price within ±0.1%.
The most-recent signal (later in the input list) survives; earlier duplicates are returned
in the deduped list.

The in-memory function handles duplicates generated during the same scheduler tick.
The DB-backed function handles duplicates against signals already approved in recent
ticks, so the bot cannot spend the daily quota on the same setup every 15 minutes.
"""

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select

from src.database import AsyncSessionLocal
from src.models.signal import ApprovedSignalORM, CandidateSignalORM
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)


def _signals_match(
    signal: CandidateSignal,
    existing: CandidateSignalORM,
    price_tolerance: float = 0.001,
) -> bool:
    """Return True when signal duplicates an existing candidate by project rules."""
    if existing.strategy != signal.strategy.value:
        return False
    if existing.direction != signal.direction.value:
        return False

    existing_entry = float(existing.entry_price)
    if existing_entry <= 0:
        return False

    return abs(signal.entry_price - existing_entry) / existing_entry < price_tolerance


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


async def dedup_against_recent_approvals(
    signals: list[CandidateSignal],
    cooldown_minutes: int = 60,
) -> tuple[list[CandidateSignal], list[CandidateSignal]]:
    """Remove signals duplicating already-approved signals in the cooldown window.

    This complements dedup_signals(), which only sees the current scheduler tick.
    Existing approved signals remain untouched for auditability; only the new
    duplicate signal is returned in the deduped list so callers persist it as
    CandidateSignal.status = DEDUPED.
    """
    if not signals:
        return [], []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)
    async with AsyncSessionLocal() as session:
        stmt = (
            select(CandidateSignalORM, ApprovedSignalORM)
            .join(
                ApprovedSignalORM,
                ApprovedSignalORM.candidate_signal_id == CandidateSignalORM.id,
            )
            .where(ApprovedSignalORM.created_at >= cutoff)
        )
        result = await session.execute(stmt)
        recent_approved = result.all()

    survivors: list[CandidateSignal] = []
    deduped: list[CandidateSignal] = []

    for signal in signals:
        duplicate_row = next(
            (
                (existing, approved)
                for existing, approved in recent_approved
                if _signals_match(signal, existing)
            ),
            None,
        )
        if duplicate_row is None:
            survivors.append(signal)
            continue

        existing, approved = duplicate_row
        deduped.append(signal)
        log.info(
            "pipeline.dedup_recent_approval",
            strategy=signal.strategy.value,
            direction=signal.direction.value,
            entry=signal.entry_price,
            existing_entry=float(existing.entry_price),
            existing_approved_id=str(approved.id),
        )

    return survivors, deduped
