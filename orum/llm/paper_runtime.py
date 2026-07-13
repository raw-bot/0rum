"""Audited boundary from validated LLM decisions to the isolated simulator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from orum.llm.contracts import ProposedDecision
from orum.llm.journal import JsonlJournal
from orum.llm.leverage import LeverageResult
from orum.llm.paper_simulator import LlmPaperSimulator
from orum.llm.paper_store import LlmPaperStore
from orum.llm.paper_validator import PaperDecisionValidator


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    decision_id: str
    lane: str
    status: str
    reasons: tuple[str, ...]
    fill_ids: tuple[str, ...]


class PaperLaneExecutor:
    """Validate, journal and commit one idempotent paper decision."""

    def __init__(
        self,
        *,
        store: LlmPaperStore,
        simulator: LlmPaperSimulator,
        validator: PaperDecisionValidator,
        audit_journal: JsonlJournal,
        starting_balance_usd: float,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.store = store
        self.simulator = simulator
        self.validator = validator
        self.audit_journal = audit_journal
        self.starting_balance_usd = starting_balance_usd
        self._clock = clock

    def execute(
        self,
        *,
        decision: ProposedDecision,
        leverage: LeverageResult | None,
        market_price: float,
        snapshot_id: str,
        snapshot_hash: str,
        snapshot_cutoff: datetime,
        candle_ts: int,
    ) -> PaperExecutionResult:
        account = self.store.load(
            decision.lane, starting_balance_usd=self.starting_balance_usd
        )
        if decision.decision_id in account.processed_decision_ids:
            return PaperExecutionResult(
                decision.decision_id, decision.lane, "already_processed", (), ()
            )
        now = self._clock().astimezone(UTC)
        report = self.validator.validate(
            decision=decision,
            account=account,
            leverage=leverage,
            market_price=market_price,
            snapshot_cutoff=snapshot_cutoff,
            now=now,
        )
        base = {
            "schema_version": 1,
            "recorded_at": now.isoformat(),
            "decision_id": decision.decision_id,
            "lane": decision.lane,
            "snapshot_id": snapshot_id,
            "snapshot_hash": snapshot_hash,
        }
        self.audit_journal.append(
            {**base, "kind": "paper_validation", "validation": report.to_mapping()}
        )
        if not report.accepted:
            return PaperExecutionResult(
                decision.decision_id, decision.lane, "rejected", report.reasons, ()
            )
        simulation = self.simulator.apply_decision(
            account,
            decision,
            leverage,
            price=market_price,
            candle_ts=candle_ts,
        )
        committed = self.store.commit(account, simulation)
        fill_ids = tuple(fill.fill_id for fill in simulation.fills)
        self.audit_journal.append(
            {
                **base,
                "kind": "paper_execution",
                "status": "executed",
                "fill_ids": list(fill_ids),
                "balance_after_usd": committed.balance_usd,
                "equity_after_usd": committed.equity_usd,
            }
        )
        return PaperExecutionResult(
            decision.decision_id, decision.lane, "executed", (), fill_ids
        )
