"""Signal ranker for the 0rum pipeline.

Applies composite scoring formula per CLAUDE.md §10.3:
    score = confidence*0.40 + rr_norm*0.30 + wfe*0.20 + regime_alignment*0.10

WFE fallback per D-02 (04-CONTEXT.md): 0.5 when no active optimizer_results row found.
Strategy-regime alignment map per D-03 (04-CONTEXT.md): hardcoded constant.

No direct DB writes — returns sorted (signal, score) pairs. PipelineRunner persists results.
"""

import asyncio

import structlog
from sqlalchemy import select

from src.database import AsyncSessionLocal
from src.models.optimizer_result import OptimizerResultORM
from src.models.signal_data import CandidateSignal, MarketRegime

log = structlog.get_logger(__name__)

# Maps strategy name → set of aligned MarketRegimeType values (score 1.0).
# All other strategy-regime combinations score 0.5.
# Per D-03 from 04-CONTEXT.md — this map is FIXED, not configurable.
REGIME_ALIGNMENT_MAP: dict[str, set[str]] = {
    "liquidity_sweep": {"RANGING"},
    "trend_continuation": {"TRENDING_UP", "TRENDING_DOWN"},
    "breakout_expansion": {"TRENDING_UP", "TRENDING_DOWN", "HIGH_VOL"},
    "ema_momentum": {"TRENDING_UP", "TRENDING_DOWN"},
}


async def _fetch_strategy_wfe(strategy_name: str) -> float:
    """Query optimizer_results for most recent active WFE for strategy.

    Per D-02: returns 0.5 (WFE gate minimum) if no active row found.
    Logs at INFO level when fallback is used.

    Args:
        strategy_name: Strategy name string (matches optimizer_results.strategy column).

    Returns:
        WFE value in [0, 1], or 0.5 as neutral fallback.
    """
    async with AsyncSessionLocal() as session:
        stmt = (
            select(OptimizerResultORM)
            .where(
                OptimizerResultORM.is_active.is_(True),
                OptimizerResultORM.strategy == strategy_name,
            )
            .order_by(OptimizerResultORM.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

    if row is not None:
        return float(row.wfe)

    # D-02: fallback to 0.5 — neutral, no bonus/penalty for unvalidated strategies
    log.info(
        "pipeline.wfe_fallback",
        strategy=strategy_name,
        fallback_wfe=0.5,
    )
    return 0.5


async def rank_signals(
    signals: list[CandidateSignal],
    regime: MarketRegime,
) -> list[tuple[CandidateSignal, float]]:
    """Compute composite rank score for each signal and return sorted list.

    Composite score formula (CLAUDE.md §10.3):
        score = confidence*0.40 + rr_norm*0.30 + wfe*0.20 + regime_alignment*0.10

    Where:
        confidence: signal.confidence (already 0–1)
        rr_norm: R:R ratio normalized to [0,1], capped at 4:1.
                 rr_ratio = abs(tp1_price - entry_price) / abs(entry_price - sl_price)
                 rr_norm = min(rr_ratio, 4.0) / 4.0
        wfe: strategy_recent_wfe from optimizer_results (D-02 fallback=0.5)
        regime_alignment: 1.0 if strategy is in REGIME_ALIGNMENT_MAP[strategy][regime], else 0.5

    Args:
        signals: List of CandidateSignal objects that passed conflict filter.
        regime: Current market regime from RegimeDetector.

    Returns:
        List of (CandidateSignal, score) sorted descending by score.
    """
    if not signals:
        return []

    # Fetch WFE for each unique strategy in parallel — avoids sequential DB hits
    unique_strategies = list({sig.strategy.value for sig in signals})
    wfe_values = await asyncio.gather(*[_fetch_strategy_wfe(s) for s in unique_strategies])
    wfe_map: dict[str, float] = dict(zip(unique_strategies, wfe_values))

    ranked: list[tuple[CandidateSignal, float]] = []

    for signal in signals:
        # confidence weight: 0.40
        confidence = signal.confidence

        # rr_norm weight: 0.30
        sl_distance = abs(signal.entry_price - signal.sl_price)
        if sl_distance == 0:
            rr_norm = 0.0
        else:
            rr_ratio = abs(signal.tp1_price - signal.entry_price) / sl_distance
            rr_norm = min(rr_ratio, 4.0) / 4.0

        # wfe weight: 0.20
        wfe = wfe_map[signal.strategy.value]

        # regime_alignment weight: 0.10
        aligned_regimes = REGIME_ALIGNMENT_MAP.get(signal.strategy.value, set())
        regime_alignment = 1.0 if regime.regime.value in aligned_regimes else 0.5

        # Composite score
        score = (
            confidence * 0.40
            + rr_norm * 0.30
            + wfe * 0.20
            + regime_alignment * 0.10
        )

        log.debug(
            "pipeline.ranked",
            strategy=signal.strategy.value,
            score=round(score, 4),
            confidence=confidence,
            rr_norm=round(rr_norm, 4),
            wfe=wfe,
            regime_alignment=regime_alignment,
        )

        ranked.append((signal, score))

    # Sort descending by score
    ranked.sort(key=lambda x: x[1], reverse=True)

    top_score = ranked[0][1] if ranked else 0.0
    log.info("pipeline.rank_complete", count=len(signals), top_score=round(top_score, 4))

    return ranked
