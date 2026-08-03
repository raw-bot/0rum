"""runtime_legacy_exact: replay the SHARED portfolio through the real
`orum.portfolio.paper_engine.PaperEngine` — same code, same strategy order,
same caps, same accounting.

Fidelity comes from reuse, not reimplementation: the engine object is the
production one; only its inputs are simulated:
  * every persistence path points inside the run directory (never state/);
  * the candle provider is the as_of-sliced snapshot provider;
  * `run_cycle(now=...)` is driven by the simulated 15-minute clock.

gold_cot reads state/cot_gate.json via orum.paths; run the harness with
0RUM_STATE_DIR pointing at the run directory (the CLI does this) so even that
read is isolated. Its cache being absent surfaces as the engine's normal
per-strategy error isolation — identical to a live box with a missing cache.

History note: until 2026-07-17 this replay also drove the forecast gate
(exact or vectorised). The gate was removed from production after the bounded
OOS study (backtests/reports/chantier3_forecast_gate.md) showed its KNN
quantiles underperform climatology; reference runs recorded before that date
carry a `gate` field in their outcomes. The gate never influenced decisions
in those runs (locked 310/311), so their trades remain comparable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from scripts.replay_harness.events import EventLog
from scripts.replay_harness.timeline import SimClock, SnapshotProvider, cycle_times


def run_runtime_replay(portfolio_config: dict, provider: SnapshotProvider, *,
                       run_dir: Path, start_ms: int, end_ms: int,
                       event_log: EventLog | None = None,
                       before_cycle: Callable[[datetime], None] | None = None) -> dict:
    """Drive the real PaperEngine cycle by cycle. Returns the cycle summaries
    plus the paths of the ledgers it wrote (all inside run_dir)."""
    # Imported here so the CLI can set 0RUM_STATE_DIR before orum.paths loads.
    from orum.portfolio.paper_engine import PaperEngine

    run_dir = Path(run_dir)
    ledger_dir = run_dir / "runtime_ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)

    clock = SimClock()
    engine = PaperEngine(
        portfolio_config,
        candle_provider=provider.bound_provider(clock),
        positions_path=ledger_dir / "positions.json",
        fills_path=ledger_dir / "fills.jsonl",
        equity_path=ledger_dir / "equity.jsonl",
    )

    summaries: list[dict] = []
    for now_ms in cycle_times(start_ms, end_ms):
        clock.now_ms = now_ms
        now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        if before_cycle is not None:
            before_cycle(now)
        summary = engine.run_cycle(now=now)
        summaries.append(summary)
        if event_log:
            for sid, intent in summary["intents"].items():
                if intent not in ("no_trade", "duplicate_candle"):
                    event_log.emit("portfolio_decision", baseline="runtime_legacy_exact",
                                   cycle_observed_time=now_ms, strategy_id=sid, intent=intent)
            for fill in summary["fills"]:
                event_log.emit("fill", baseline="runtime_legacy_exact",
                               cycle_observed_time=now_ms, **fill)
            for sid, err in summary.get("errors", {}).items():
                event_log.emit("strategy_error", baseline="runtime_legacy_exact",
                               cycle_observed_time=now_ms, strategy_id=sid, error=err)

    (run_dir / "runtime_summaries.json").write_text(json.dumps(summaries, indent=1, default=str))
    return {"cycles": len(summaries), "ledger_dir": str(ledger_dir),
            "final": summaries[-1] if summaries else None}
