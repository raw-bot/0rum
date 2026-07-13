"""Deterministic pre-execution checks for isolated LLM paper decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

from orum.llm.contracts import ProposedDecision
from orum.llm.leverage import LeverageResult
from orum.llm.paper_contracts import LlmPaperAccount


@dataclass(frozen=True, slots=True)
class ValidationReport:
    decision_id: str
    lane: str
    accepted: bool
    reasons: tuple[str, ...]
    paper_effective_leverage: float | None
    fr_retail_eligible_leverage: float | None
    estimated_liquidation_price: float | None

    def to_mapping(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "lane": self.lane,
            "accepted": self.accepted,
            "reasons": list(self.reasons),
            "paper_effective_leverage": self.paper_effective_leverage,
            "fr_retail_eligible_leverage": self.fr_retail_eligible_leverage,
            "estimated_liquidation_price": self.estimated_liquidation_price,
        }


class PaperDecisionValidator:
    ENTRY_ACTIONS = frozenset({"open_long", "open_short", "add"})
    POSITION_ACTIONS = frozenset({"add", "reduce", "close"})

    def __init__(
        self,
        *,
        max_snapshot_age: timedelta = timedelta(minutes=30),
        maintenance_margin_rate: float = 0.005,
        allow_stop_beyond_liquidation: bool = False,
    ) -> None:
        self.max_snapshot_age = max_snapshot_age
        self.maintenance_margin_rate = maintenance_margin_rate
        self.allow_stop_beyond_liquidation = allow_stop_beyond_liquidation

    def validate(
        self,
        *,
        decision: ProposedDecision,
        account: LlmPaperAccount,
        leverage: LeverageResult | None,
        market_price: float,
        snapshot_cutoff,
        now,
    ) -> ValidationReport:
        reasons: list[str] = []
        price = float(market_price)
        position = account.positions.get(decision.symbol)
        if decision.lane != account.lane:
            reasons.append("lane_mismatch")
        if decision.decision_id in account.processed_decision_ids:
            reasons.append("decision_already_processed")
        age = now - snapshot_cutoff
        if age < timedelta(0):
            reasons.append("snapshot_from_future")
        elif age > self.max_snapshot_age:
            reasons.append("snapshot_stale")
        if not math.isfinite(price) or price <= 0:
            reasons.append("invalid_market_price")
        if decision.order_type == "limit":
            reasons.append("limit_order_execution_not_installed")
        if decision.action in {"open_long", "open_short"} and position is not None:
            reasons.append("position_must_be_flat_for_open")
        if decision.action in self.POSITION_ACTIONS and position is None:
            reasons.append("position_required_for_action")
        if decision.action == "reduce" and not 0 < decision.equity_fraction < 1:
            reasons.append("invalid_reduce_fraction")

        liquidation = None
        if decision.action in self.ENTRY_ACTIONS:
            if leverage is None:
                reasons.append("leverage_result_required")
            else:
                side = position.side if decision.action == "add" and position else (
                    "long" if decision.action == "open_long" else "short"
                )
                if side == "long":
                    liquidation = price * (
                        1 - 1 / leverage.paper_effective + self.maintenance_margin_rate
                    )
                else:
                    liquidation = price * (
                        1 + 1 / leverage.paper_effective - self.maintenance_margin_rate
                    )
                if decision.stop_loss is not None:
                    if side == "long" and decision.stop_loss >= price:
                        reasons.append("long_stop_not_below_market")
                    if side == "short" and decision.stop_loss <= price:
                        reasons.append("short_stop_not_above_market")
                    if not self.allow_stop_beyond_liquidation and (
                        (side == "long" and decision.stop_loss <= liquidation)
                        or (side == "short" and decision.stop_loss >= liquidation)
                    ):
                        reasons.append("stop_at_or_beyond_liquidation")
                for target in decision.take_profits:
                    if side == "long" and target.price <= price:
                        reasons.append("long_target_not_above_market")
                        break
                    if side == "short" and target.price >= price:
                        reasons.append("short_target_not_below_market")
                        break
                allocated = account.equity_usd * decision.equity_fraction
                used = sum(item.initial_margin_usd for item in account.positions.values())
                if allocated <= 0 or used + allocated > account.equity_usd + 1e-9:
                    reasons.append("insufficient_isolated_collateral")

        unique_reasons = tuple(dict.fromkeys(reasons))
        return ValidationReport(
            decision_id=decision.decision_id,
            lane=decision.lane,
            accepted=not unique_reasons,
            reasons=unique_reasons,
            paper_effective_leverage=None if leverage is None else leverage.paper_effective,
            fr_retail_eligible_leverage=None if leverage is None else leverage.fr_retail_eligible,
            estimated_liquidation_price=liquidation,
        )
