"""RiskGateRunner — orchestrates the breaker check, risk gates, and ATR sizer.

Per CONTEXT D-12: tripped breaker short-circuits with reason='circuit_breaker_active'.
Per D-15: RISK-01 rejection does NOT call breaker.record_stop.
Concentration can reduce size at a soft threshold or block at a hard threshold.
"""

from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.market.instruments import get_instrument_spec
from src.models.signal_data import CandidateSignal, MarketRegime
from src.risk.account import (
    get_current_equity,
    get_max_drawdown_pct,
    get_open_exposure,
)
from src.risk.breaker import BreakerManager
from src.risk.events import RiskDecision
from src.risk.gates import (
    count_same_direction_open,
    evaluate_daily_loss,
    evaluate_max_positions,
)
from src.risk.sizer import calculate_position_size

log = structlog.get_logger(__name__)


def _candidate_stop_risk_distance(candidate: CandidateSignal) -> Decimal:
    entry_price = Decimal(str(candidate.entry_price))
    sl_price = Decimal(str(candidate.sl_price))
    if candidate.direction.value == "BUY":
        return max(entry_price - sl_price, Decimal("0"))
    return max(sl_price - entry_price, Decimal("0"))


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

        if await self.breaker.is_kill_switch_active():
            log.warning(
                "risk.gate.rejected",
                gate="kill_switch",
                reason="kill_switch_active",
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(passed=False, reason="kill_switch_active")

        equity = await get_current_equity(
            session,
            starting_balance=self.settings.theoretical_equity_usd,
        )
        if equity <= 0:
            log.info(
                "risk.gate.rejected",
                gate="equity",
                reason="non_positive_equity",
                equity_usd=str(equity),
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(
                passed=False,
                reason="non_positive_equity",
                equity_usd=equity,
            )

        # Gate 1: daily loss (RISK-01) — does NOT trip the breaker (D-15).
        passed, daily_pnl = await evaluate_daily_loss(
            session,
            self.settings.daily_loss_limit,
            fallback_equity=equity,
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
            return RiskDecision(
                passed=False,
                reason="daily_loss_limit",
                equity_usd=equity,
            )

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
            return RiskDecision(
                passed=False,
                reason="max_positions",
                equity_usd=equity,
            )

        drawdown_pct = await get_max_drawdown_pct(
            session,
            starting_balance=self.settings.theoretical_equity_usd,
        )
        if drawdown_pct >= self.settings.max_equity_drawdown_pct:
            log.info(
                "risk.gate.rejected",
                gate="max_equity_drawdown",
                reason="max_equity_drawdown",
                drawdown_pct=str(drawdown_pct),
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(
                passed=False,
                reason="max_equity_drawdown",
                equity_usd=equity,
            )

        # Gate 3: concentration reduces at the soft threshold and blocks at hard threshold.
        same_dir_count = await count_same_direction_open(
            session, candidate.direction.value
        )
        if same_dir_count >= self.settings.concentration_block_at:
            log.info(
                "risk.gate.rejected",
                gate="concentration",
                reason="concentration_limit",
                same_direction_open_count=same_dir_count,
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(
                passed=False,
                reason="concentration_limit",
                equity_usd=equity,
            )

        # Sizer (RISK-04).
        sizing = calculate_position_size(
            equity=equity,
            risk_per_trade=self.settings.risk_per_trade,
            entry_price=Decimal(str(candidate.entry_price)),
            sl_price=Decimal(str(candidate.sl_price)),
            atr_value=Decimal(str(regime.atr_value)),
            atr_pctile=regime.atr_pctile,
            hard_cap=self.settings.hard_cap_risk,
            atr_high_vol_pctile=self.settings.atr_high_vol_percentile,
            atr_low_vol_pctile=self.settings.atr_low_vol_percentile,
            same_direction_open_count=same_dir_count,
            concentration_reduce_at=self.settings.concentration_reduce_at,
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

        spec = get_instrument_spec("XAUUSD")
        entry_price = Decimal(str(candidate.entry_price))
        sl_price = Decimal(str(candidate.sl_price))
        candidate_notional = entry_price * sizing.size_lots * spec.contract_size
        candidate_stop_risk = (
            _candidate_stop_risk_distance(candidate)
            * sizing.size_lots
            * spec.contract_size
        )
        open_notional, open_stop_risk = await get_open_exposure(session)
        notional_after = open_notional + candidate_notional
        stop_risk_after = open_stop_risk + candidate_stop_risk
        exposure_multiple_after = notional_after / equity
        stop_risk_pct_after = stop_risk_after / equity

        if exposure_multiple_after > self.settings.max_account_leverage:
            log.info(
                "risk.gate.rejected",
                gate="max_account_leverage",
                reason="max_account_leverage",
                exposure_multiple_after=str(exposure_multiple_after),
                equity_usd=str(equity),
                notional_after_usd=str(notional_after),
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(
                passed=False,
                reason="max_account_leverage",
                equity_usd=equity,
                notional_after_usd=notional_after,
                stop_risk_after_usd=stop_risk_after,
                exposure_multiple_after=exposure_multiple_after,
                candidate_notional_usd=candidate_notional,
            )

        if stop_risk_pct_after > self.settings.max_stop_risk_pct:
            log.info(
                "risk.gate.rejected",
                gate="max_stop_risk",
                reason="max_stop_risk",
                stop_risk_pct_after=str(stop_risk_pct_after),
                equity_usd=str(equity),
                stop_risk_after_usd=str(stop_risk_after),
                strategy=candidate.strategy.value,
                direction=candidate.direction.value,
                entry_price=candidate.entry_price,
                signal_ref=id(candidate),
            )
            return RiskDecision(
                passed=False,
                reason="max_stop_risk",
                equity_usd=equity,
                notional_after_usd=notional_after,
                stop_risk_after_usd=stop_risk_after,
                exposure_multiple_after=exposure_multiple_after,
                candidate_notional_usd=candidate_notional,
            )

        return RiskDecision(
            passed=True,
            reason=None,
            sizing=sizing,
            concentration_reduced=sizing.concentration_reduced,
            equity_usd=equity,
            notional_after_usd=notional_after,
            stop_risk_after_usd=stop_risk_after,
            exposure_multiple_after=exposure_multiple_after,
            candidate_notional_usd=candidate_notional,
        )
