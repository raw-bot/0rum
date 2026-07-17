"""runtime_legacy_exact: replay the SHARED portfolio through the real
`orum.portfolio.paper_engine.PaperEngine` — same code, same strategy order,
same caps, same forecast gate (with its fail-open), same accounting.

Fidelity comes from reuse, not reimplementation: the engine object is the
production one; only its inputs are simulated:
  * every persistence path points inside the run directory (never state/);
  * the candle provider is the as_of-sliced snapshot provider;
  * `run_cycle(now=...)` is driven by the simulated 15-minute clock.

The forecast evaluator is the real `walk_forward_forecast`, memoized on
(symbol, last 1h candle ts): the report is a pure function of the candle
history, so caching changes nothing but runtime cost.

gold_cot reads state/cot_gate.json via orum.paths; run the harness with
0RUM_STATE_DIR pointing at the run directory (the CLI does this) so even that
read is isolated. Its cache being absent surfaces as the engine's normal
per-strategy error isolation — identical to a live box with a missing cache.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.replay_harness.events import EventLog
from scripts.replay_harness.timeline import SimClock, SnapshotProvider, cycle_times


def _memoized_forecast(evaluator):
    """Memoize on the CONTENT of the candle window, never on its shape.

    The evaluator signature carries no symbol, and two different assets served
    by the same provider are guaranteed to collide on (length, last ts): both
    are capped at the same history_limit and close on the same hour grid. The
    key therefore samples the series itself — endpoints plus every 50th
    (ts, close) pair — so aligned-but-different series can never share a
    result, while identical windows still hit the cache."""
    cache: dict = {}
    def wrapped(candles, **kwargs):
        content = tuple((c["ts"], c["close"]) for c in candles[::50])
        key = (len(candles),
               candles[0]["ts"] if candles else None,
               candles[-1]["ts"] if candles else None,
               candles[-1]["close"] if candles else None,
               content,
               tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = evaluator(candles, **kwargs)
        return cache[key]
    return wrapped


def run_runtime_replay(portfolio_config: dict, provider: SnapshotProvider, *,
                       run_dir: Path, start_ms: int, end_ms: int,
                       event_log: EventLog | None = None,
                       gate: str = "exact") -> dict:
    """Drive the real PaperEngine cycle by cycle. Returns the cycle summaries
    plus the paths of the ledgers it wrote (all inside run_dir).

    gate="exact" uses the production walk_forward_forecast (slow, ~4s/eval);
    gate="fast" swaps in fast_gate.walk_forward_forecast_fast — decision
    parity is enforced by tests/test_fast_gate_parity.py, enabling multi-year
    windows. The baseline named runtime_legacy_exact must use "exact"."""
    # Imported here so the CLI can set 0RUM_STATE_DIR before orum.paths loads.
    from orum.portfolio.forecast_gate import walk_forward_forecast
    from orum.portfolio.paper_engine import PaperEngine

    if gate == "fast":
        from scripts.replay_harness.fast_gate import walk_forward_forecast_fast
        evaluator = walk_forward_forecast_fast
    elif gate == "exact":
        evaluator = walk_forward_forecast
    else:
        raise ValueError(f"unknown gate mode {gate!r}")

    run_dir = Path(run_dir)
    ledger_dir = run_dir / "runtime_ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: ledger_dir / f"{name}.json" for name in
             ("positions", "forecast_state")}
    jsonl = {name: ledger_dir / f"{name}.jsonl" for name in
             ("fills", "equity", "forecast_audit", "forecast_history")}

    clock = SimClock()
    engine = PaperEngine(
        portfolio_config,
        candle_provider=provider.bound_provider(clock),
        positions_path=paths["positions"],
        fills_path=jsonl["fills"],
        equity_path=jsonl["equity"],
        forecast_state_path=paths["forecast_state"],
        forecast_audit_path=jsonl["forecast_audit"],
        forecast_history_path=jsonl["forecast_history"],
        forecast_evaluator=_memoized_forecast(evaluator),
    )

    summaries: list[dict] = []
    for now_ms in cycle_times(start_ms, end_ms):
        clock.now_ms = now_ms
        summary = engine.run_cycle(now=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc))
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
