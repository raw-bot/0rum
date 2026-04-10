"""Quota gate for the 0rum signal pipeline.

Enforces MAX_SIGNALS_PER_DAY=5 per UTC calendar day per CLAUDE.md §10.4.
Queries approved_signals COUNT for the current UTC day to determine remaining slots.

No direct DB writes — returns (approved_ranked, quota_rejected) pairs.
PipelineRunner persists results atomically.
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select

from src.database import AsyncSessionLocal
from src.models.signal import ApprovedSignalORM
from src.models.signal_data import CandidateSignal

log = structlog.get_logger(__name__)


async def _count_today_approvals() -> int:
    """Count approved signals already created today (UTC calendar day).

    Returns:
        Count of ApprovedSignalORM rows where DATE(created_at) = today UTC.
    """
    today = datetime.now(timezone.utc).date()
    async with AsyncSessionLocal() as session:
        stmt = (
            select(func.count())
            .select_from(ApprovedSignalORM)
            .where(func.date(ApprovedSignalORM.created_at) == today)
        )
        result = await session.execute(stmt)
        return result.scalar_one()


async def apply_quota(
    ranked_signals: list[tuple[CandidateSignal, float]],
    max_per_day: int = 5,
) -> tuple[list[tuple[CandidateSignal, float]], list[CandidateSignal]]:
    """Enforce MAX_SIGNALS_PER_DAY quota for current UTC calendar day.

    Queries approved_signals WHERE DATE(created_at AT TIME ZONE 'UTC') = TODAY.
    Counts existing approved signals for today.
    Allows only (max_per_day - existing_count) signals through from ranked_signals.
    The rest are moved to the rejected list.

    Args:
        ranked_signals: List of (CandidateSignal, score) sorted descending by score.
        max_per_day: Maximum approved signals per UTC calendar day (default 5).

    Returns:
        Tuple of (approved_ranked, quota_rejected) where:
            approved_ranked: list of (CandidateSignal, score) that passed quota
            quota_rejected: list of CandidateSignal that were quota-blocked
    """
    existing = await _count_today_approvals()
    slots_remaining = max(0, max_per_day - existing)

    approved = ranked_signals[:slots_remaining]
    rejected = [sig for sig, _ in ranked_signals[slots_remaining:]]

    if rejected:
        log.info(
            "pipeline.quota_enforced",
            existing_today=existing,
            slots_remaining=slots_remaining,
            rejected_count=len(rejected),
        )

    return approved, rejected
