"""PipelineRunner — full signal pipeline coordinator for 0rum.

Orchestrates all five pipeline steps:
    dedup → conflict → regime → rank → quota → persist

Per D-04 (04-CONTEXT.md): All signals are processed in-memory. A single DB transaction
at the end writes all CandidateSignalORM rows (with final statuses) and ApprovedSignalORM rows.

Provider-agnostic: no broker/provider-specific code here (D-05).
"""

import structlog

from src.backtesting.regime_detector import RegimeDetector
from src.config import get_settings
from src.database import AsyncSessionLocal
from src.models.regime import MarketRegimeORM
from src.models.signal import ApprovedSignalORM, CandidateSignalORM
from src.models.signal_data import CandidateSignal, MarketRegime
from src.pipeline.conflict_filter import filter_conflicts
from src.pipeline.dedup import dedup_signals
from src.pipeline.quota import apply_quota
from src.pipeline.ranker import rank_signals

log = structlog.get_logger(__name__)


class PipelineRunner:
    """Orchestrates the full signal pipeline: dedup → conflict → regime → rank → quota → persist.

    Per D-04 (04-CONTEXT.md): All signals are processed in-memory. A single DB transaction
    at the end writes all CandidateSignalORM rows (with final statuses) and ApprovedSignalORM rows.

    Sequence:
        1. dedup_signals() — removes duplicates within cooldown window
        2. filter_conflicts() — resolves opposing BUY/SELL by confidence
        3. RegimeDetector.detect() — classifies current market regime from H1 candles
        4. rank_signals() — scores each signal, returns sorted (signal, score) list
        5. apply_quota() — enforces MAX_SIGNALS_PER_DAY gate
        6. _persist() — single atomic DB transaction
    """

    async def run(
        self,
        candidates: list[CandidateSignal],
        h1_candles: list,
    ) -> list[ApprovedSignalORM]:
        """Run the full pipeline.

        Args:
            candidates: list[CandidateSignal] from StrategyRunner.run()
            h1_candles: list of Candle ORM objects (H1 timeframe, oldest→newest)
                        used by RegimeDetector for ATR/ADX/EMA calculations.

        Returns:
            list[ApprovedSignalORM] — the persisted approved signals (IDs populated by DB).
        """
        if not candidates:
            log.info("pipeline.runner.no_candidates")
            return []

        log.info("pipeline.runner.start", candidate_count=len(candidates))

        # Step 1: Dedup
        survivors, deduped = dedup_signals(candidates)
        log.info(
            "pipeline.runner.after_dedup",
            survivors=len(survivors),
            deduped=len(deduped),
        )

        # Step 2: Conflict filter
        kept, conflict_rejected = filter_conflicts(survivors)
        log.info(
            "pipeline.runner.after_conflict",
            kept=len(kept),
            rejected=len(conflict_rejected),
        )

        # Step 3: Regime detection
        regime = await RegimeDetector().detect(h1_candles)

        # Step 4: Rank
        ranked = await rank_signals(kept, regime)

        # Step 5: Quota
        settings = get_settings()
        approved_ranked, quota_rejected = await apply_quota(
            ranked, max_per_day=settings.max_signals_per_day
        )

        # Build status map: id(signal) → final status string
        # All candidates start PENDING; update based on pipeline outcome
        status_map: dict[int, str] = {}
        for sig in deduped:
            status_map[id(sig)] = "DEDUPED"
        for sig in conflict_rejected:
            status_map[id(sig)] = "REJECTED"
        for sig in quota_rejected:
            status_map[id(sig)] = "REJECTED"
        for sig, _ in approved_ranked:
            status_map[id(sig)] = "APPROVED"

        # Step 6: Persist all data in a single atomic transaction (D-04)
        approved_orms = await self._persist(candidates, approved_ranked, regime, status_map)

        log.info(
            "pipeline.runner.complete",
            total_candidates=len(candidates),
            approved=len(approved_orms),
            deduped=len(deduped),
            conflict_rejected=len(conflict_rejected),
            quota_rejected=len(quota_rejected),
        )
        return approved_orms

    async def _persist(
        self,
        all_candidates: list[CandidateSignal],
        approved_ranked: list[tuple[CandidateSignal, float]],
        regime: MarketRegime,
        status_map: dict[int, str],
    ) -> list[ApprovedSignalORM]:
        """Write all data in a single atomic DB transaction (per D-04).

        Writes in order:
            1. MarketRegimeORM row
            2. All CandidateSignalORM rows with final statuses
            3. ApprovedSignalORM rows linked to the candidate IDs

        session.flush() is called after candidate inserts to get DB-generated UUIDs
        before creating ApprovedSignalORM foreign key references.

        Args:
            all_candidates: All original CandidateSignal objects (complete audit trail).
            approved_ranked: Signals that passed quota with their scores.
            regime: Detected market regime to persist alongside signals.
            status_map: Mapping of id(CandidateSignal) → final status string.

        Returns:
            list[ApprovedSignalORM] with IDs populated by DB after flush.
        """
        async with AsyncSessionLocal() as session:
            async with session.begin():
                # 1. Persist regime
                regime_orm = MarketRegimeORM(
                    timestamp=regime.timestamp,
                    regime=regime.regime.value,
                    atr_value=regime.atr_value,
                    atr_pctile=regime.atr_pctile,
                    adx_value=regime.adx_value,
                )
                session.add(regime_orm)

                # 2. Persist all candidate signals with final status
                candidate_orms: dict[int, CandidateSignalORM] = {}
                for sig in all_candidates:
                    final_status = status_map.get(id(sig), "PENDING")
                    orm = CandidateSignalORM(
                        strategy=sig.strategy.value,
                        direction=sig.direction.value,
                        entry_price=sig.entry_price,
                        sl_price=sig.sl_price,
                        tp1_price=sig.tp1_price,
                        tp2_price=sig.tp2_price,
                        confidence=sig.confidence,
                        timeframe=sig.timeframe.value,
                        params_snapshot=sig.params_snapshot,
                        status=final_status,
                    )
                    session.add(orm)
                    candidate_orms[id(sig)] = orm

                # Flush to get DB-generated UUIDs for candidates before creating approved links
                await session.flush()

                # 3. Persist approved signals
                approved_orms: list[ApprovedSignalORM] = []
                for sig, score in approved_ranked:
                    cand_orm = candidate_orms[id(sig)]
                    approved_orm = ApprovedSignalORM(
                        candidate_signal_id=cand_orm.id,
                        rank_score=score,
                        risk_check_passed=True,
                        execution_status="PENDING",
                    )
                    session.add(approved_orm)
                    approved_orms.append(approved_orm)

                await session.flush()

        return approved_orms
