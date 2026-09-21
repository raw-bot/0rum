"""Audited boundary from validated LLM decisions to the isolated simulator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from orum.llm.contracts import ProposedDecision
from orum.llm.journal import JsonlJournal
from orum.llm.leverage import LeverageResult
from orum.llm.paper_contracts import LlmPaperFill
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

    def decision_status(self, *, lane: str, decision_id: str) -> str | None:
        account = self.store.load(lane, starting_balance_usd=self.starting_balance_usd)
        if decision_id in account.processed_decision_ids:
            return "executed"
        for record in reversed(self.audit_journal.read()):
            if record.get("lane") != lane or record.get("decision_id") != decision_id:
                continue
            if record.get("kind") == "paper_execution":
                return "executed"
            validation = record.get("validation")
            if record.get("kind") == "paper_validation" and isinstance(validation, dict):
                if validation.get("accepted") is False:
                    return "rejected"
        return None

    def monitor(self, *, lane: str, candle: dict) -> tuple:
        account = self.store.load(lane, starting_balance_usd=self.starting_balance_usd)
        if not account.positions:
            return ()
        simulation = self.simulator.monitor_candle(account, candle)
        if simulation.account.to_mapping() == account.to_mapping() and not simulation.fills:
            return ()
        committed = self.store.commit(account, simulation)
        if simulation.fills:
            self.audit_journal.append({
                "schema_version": 1,
                "kind": "paper_monitor",
                "recorded_at": self._clock().astimezone(UTC).isoformat(),
                "lane": lane,
                "candle_ts": int(candle["ts"]),
                "fill_ids": [fill.fill_id for fill in simulation.fills],
                "actions": [fill.action for fill in simulation.fills],
                "balance_after_usd": committed.balance_usd,
                "equity_after_usd": committed.equity_usd,
            })
        return simulation.fills

    def learning_fills(self) -> list[LlmPaperFill]:
        """Terminal fill per position, so partial targets cannot truncate outcomes."""
        positions = {}
        for row in self.store.fills.read():
            position = positions.setdefault(row["position_id"], {"qty": 0.0, "terminal": None})
            if row["action"] in {"open", "add"}:
                position["qty"] += float(row["qty"])
            else:
                position["qty"] -= float(row["qty"])
                position["terminal"] = row
        return [LlmPaperFill.from_mapping(value["terminal"]) for value in positions.values()
                if value["terminal"] is not None and abs(value["qty"]) <= 1e-10]
