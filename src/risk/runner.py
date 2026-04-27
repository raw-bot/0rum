"""RiskGateRunner — orchestrates the breaker check + 3 risk gates + ATR sizer.

Per CONTEXT D-12: tripped breaker short-circuits with reason='circuit_breaker_active'.
Per D-15: RISK-01 rejection does NOT call breaker.record_stop.
Per D-04: concentration is not a block — sets concentration_reduced on the decision.
"""

from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.signal_data import CandidateSignal, MarketRegime
from src.risk.breaker import BreakerManager
from src.risk.events import RiskDecision
from src.risk.gates import (
    count_same_direction_open,
    evaluate_daily_loss,
    evaluate_max_positions,
)
from src.risk.sizer import calculate_position_size

log = structlog.get_logger(__name__)


class RiskGateRunner:
    def __init__(self, breaker: BreakerManager | None = None):
        self.breaker = breaker or BreakerManager()
        self.settings = get_settings()

    async def evaluate(
        self,
        candidate: CandidateSignal,
        regime: MarketRegime,
        session: AsyncSession,
    ) -> RiskDecision:
        # Idempotent: clears state if cooldown elapsed.
        await self.breaker.reset_if_expired()

        # D-12: short-circuit before any gate runs.
        if await self.breaker.is_tripped():
            log.info(
                "risk.gate.rejected",
                gate="circuit_breaker",
                reason="circuit_breaker_active",
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(passed=False, reason="circuit_breaker_active")

        # Gate 1: daily loss (RISK-01) — does NOT trip the breaker (D-15).
        passed, daily_pnl = await evaluate_daily_loss(
            session, self.settings.daily_loss_limit
        )
        if not passed:
            log.info(
                "risk.gate.rejected",
                gate="daily_loss",
                reason="daily_loss_limit",
                daily_pnl_pct=daily_pnl,
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(passed=False, reason="daily_loss_limit")

        # Gate 2: max positions (RISK-02).
        passed, open_count = await evaluate_max_positions(
            session, self.settings.max_positions
        )
        if not passed:
            log.info(
                "risk.gate.rejected",
                gate="max_positions",
                reason="max_positions",
                open_count=open_count,
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(passed=False, reason="max_positions")

        # Gate 3 (count only): concentration — REDUCE not BLOCK (D-04).
        same_dir_count = await count_same_direction_open(
            session, candidate.direction.value
        )

        # Sizer (RISK-04).
        sizing = calculate_position_size(
            equity=self.settings.theoretical_equity_usd,
            risk_per_trade=self.settings.risk_per_trade,
            entry_price=Decimal(str(candidate.entry_price)),
            sl_price=Decimal(str(candidate.sl_price)),
            atr_value=Decimal(str(regime.atr_value)),
            atr_pctile=regime.atr_pctile,
            hard_cap=self.settings.hard_cap_risk,
            atr_high_vol_pctile=self.settings.atr_high_vol_percentile,
            atr_low_vol_pctile=self.settings.atr_low_vol_percentile,
            same_direction_open_count=same_dir_count,
        )
        log.info(
            "risk.sizing.calculated",
            strategy=candidate.strategy.value,
            direction=candidate.direction.value,
            risk_pct=sizing.risk_pct,
            size_lots=str(sizing.size_lots),
            vol_factor=sizing.vol_factor,
            concentration_reduced=sizing.concentration_reduced,
        )

        return RiskDecision(
            passed=True,
            reason=None,
            sizing=sizing,
            concentration_reduced=sizing.concentration_reduced,
        )
