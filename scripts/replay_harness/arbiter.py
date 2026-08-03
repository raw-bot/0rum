"""Replay adapter for the production thesis-budget auction.

The paper engine owns allocation, tranche management and stop-risk accounting.
This module only injects replay-specific policy arguments and drives the
simulated clock, preventing research and paper behavior from drifting.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.replay_harness.events import EventLog
from scripts.replay_harness.timeline import SimClock, SnapshotProvider, cycle_times


TRANCHE_SEP = "::t"


def base_strategy_id(position_key: str) -> str:
    """Map a legacy tranche key back to its stable strategy id."""
    return position_key.split(TRANCHE_SEP, 1)[0]


def make_arbiter_engine_class():
    """Defer imports until the replay CLI has selected its state directory."""
    from orum.portfolio.paper_engine import PaperEngine

    class ThesisArbiterEngine(PaperEngine):
        def __init__(
            self,
            config: dict,
            *,
            reentry_policy: str = "hold",
            merit_order: list[str] | None = None,
            min_topup_fraction: float = 0.0,
            **kwargs,
        ) -> None:
            configured = dict(config)
            configured.update({
                "reentry_policy": reentry_policy,
                "merit_order": list(merit_order or []),
                "min_topup_fraction": float(min_topup_fraction),
            })
            super().__init__(configured, **kwargs)

    return ThesisArbiterEngine


def run_arbiter_replay(
    portfolio_config: dict,
    provider: SnapshotProvider,
    *,
    run_dir: Path,
    start_ms: int,
    end_ms: int,
    event_log: EventLog | None = None,
    reentry_policy: str = "hold",
    merit_order: list[str] | None = None,
    min_topup_fraction: float = 0.0,
) -> dict:
    """Drive the production auction cycle by cycle inside an isolated ledger."""
    run_dir = Path(run_dir)
    ledger_dir = run_dir / "runtime_ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)

    clock = SimClock()
    engine_cls = make_arbiter_engine_class()
    engine = engine_cls(
        portfolio_config,
        candle_provider=provider.bound_provider(clock),
        positions_path=ledger_dir / "positions.json",
        fills_path=ledger_dir / "fills.jsonl",
        equity_path=ledger_dir / "equity.jsonl",
        reentry_policy=reentry_policy,
        merit_order=merit_order,
        min_topup_fraction=min_topup_fraction,
    )

    summaries: list[dict] = []
    for now_ms in cycle_times(start_ms, end_ms):
        clock.now_ms = now_ms
        summary = engine.run_cycle(
            now=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        )
        summaries.append(summary)
        if event_log:
            for strategy_id, intent in summary["intents"].items():
                if intent not in ("no_trade", "duplicate_candle"):
                    event_log.emit(
                        "portfolio_decision",
                        baseline="thesis_arbiter",
                        cycle_observed_time=now_ms,
                        strategy_id=strategy_id,
                        intent=intent,
                    )
            for record in summary.get("auction", []):
                event_log.emit(
                    "auction",
                    baseline="thesis_arbiter",
                    cycle_observed_time=now_ms,
                    **record,
                )
            for fill in summary["fills"]:
                event_log.emit(
                    "fill",
                    baseline="thesis_arbiter",
                    cycle_observed_time=now_ms,
                    **fill,
                )
            for strategy_id, error in summary.get("errors", {}).items():
                event_log.emit(
                    "strategy_error",
                    baseline="thesis_arbiter",
                    cycle_observed_time=now_ms,
                    strategy_id=strategy_id,
                    error=error,
                )

    (run_dir / "runtime_summaries.json").write_text(
        json.dumps(summaries, indent=1, default=str)
    )
    return {
        "cycles": len(summaries),
        "ledger_dir": str(ledger_dir),
        "arbiter": {
            "reentry_policy": reentry_policy,
            "merit_order": merit_order,
            "min_topup_fraction": min_topup_fraction,
        },
        "final": summaries[-1] if summaries else None,
    }
