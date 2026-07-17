"""thesis_arbiter: runtime-replay VARIANT where each cycle's entry intents are
collected FIRST, then the (symbol, direction) risk budget is allocated by
merit — an auction, not a first-come queue.

What changes vs the production engine (and ONLY this):
  * Phase 2b entry allocation. The baseline walks strategies in config order
    and hard-refuses any candidate whose full request would breach a cap
    (first-come-first-served + all-or-nothing). The arbiter gathers every
    fundable LONG intent of the cycle, sorts them by merit, and grants each
    one min(requested, remaining thesis budget, remaining total budget):
    a second signal on an already-funded thesis TOPS UP the remaining budget
    instead of stacking on top of it; when nothing remains the refusal reason
    is `thesis_already_funded` (or `risk_cap_total` when the portfolio-wide
    cap is the binding constraint).
  * Re-entry while holding (the AK auto-collision: risk 2% + 2% > cap 3%,
    an implicit never-decided rule) becomes an EXPLICIT policy:
      - "hold"  : a strategy already holding never adds (same outcome as the
                  baseline, but the refusal is named `reentry_hold` instead of
                  hiding behind `risk_cap_symbol`);
      - "topup" : the re-entry opens a SEPARATE tranche sized to the remaining
                  thesis budget. Each tranche keeps its own SL/TP bracket, so
                  no bracket merging ambiguity exists; a strategy EXIT signal
                  closes every tranche.
  * The forecast gate influences (and is audited for) genuine new entries
    only, exactly like the baseline: a held strategy's top-up request uses its
    plain configured risk_pct.

Everything else — Phase 1 signal gathering, the forecast refresh block,
protective exits, accounting, persistence — is copied verbatim from
`PaperEngine.run_cycle` (protective exits generalised to iterate tranches,
which degenerates to the original loop when no tranche exists).

This module lives in the harness on purpose: the launchd worker reloads the
working tree every cycle, so editing orum/ would be a de facto live deploy.
Promotion to production is a separate, explicit user decision.

Merit v1 is a static ranking (CLI --merit, default: config order), matching
the acted portfolio decision "UTBot core, AK satellite". A learned/rolling
merit is future work and must stay ex ante.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.replay_harness.events import EventLog
from scripts.replay_harness.timeline import SimClock, SnapshotProvider, cycle_times


TRANCHE_SEP = "::t"


def base_strategy_id(position_key: str) -> str:
    """Map a position/fill key (possibly a tranche key) back to its strategy id."""
    return position_key.split(TRANCHE_SEP, 1)[0]


def make_arbiter_engine_class():
    """Deferred class construction so the CLI can set 0RUM_STATE_DIR before
    orum.paths freezes (same constraint as runtime_replay)."""
    from orum.portfolio.forecast_gate import decide_forecast_gate
    from orum.portfolio.forecast_history import due_realization_records, prediction_record
    from orum.portfolio.paper_engine import _ATR_MULT, PaperEngine, StrategyConfig, _atr
    from orum.strategies.base import Side, StrategyContext
    from orum.fsio import atomic_write_json

    class ThesisArbiterEngine(PaperEngine):
        def __init__(self, *args, reentry_policy: str = "hold",
                     merit_order: list[str] | None = None,
                     min_topup_fraction: float = 0.0, **kwargs) -> None:
            if reentry_policy not in ("hold", "topup"):
                raise ValueError(f"unknown reentry_policy {reentry_policy!r}")
            super().__init__(*args, **kwargs)
            self._reentry_policy = reentry_policy
            self._min_topup_fraction = float(min_topup_fraction)
            configured = {sc.id for sc in self._strategies}
            unknown = set(merit_order or []) - configured
            if unknown:
                raise ValueError(f"merit_order references unknown strategies: {sorted(unknown)}")
            config_rank = {sc.id: i for i, sc in enumerate(self._strategies)}
            merit_rank = {sid: i for i, sid in enumerate(merit_order or [])}
            # merit list first, then config order; deterministic by construction.
            self._merit_key = {
                sc.id: (merit_rank.get(sc.id, len(merit_rank)), config_rank[sc.id])
                for sc in self._strategies
            }

        # ---- tranche helpers ------------------------------------------------
        def _tranche_keys(self, account, strategy_id: str) -> list[str]:
            prefix = strategy_id + TRANCHE_SEP
            return [key for key in account.positions
                    if key == strategy_id or key.startswith(prefix)]

        def _next_tranche_key(self, account, strategy_id: str) -> str:
            if strategy_id not in account.positions:
                return strategy_id
            n = 2
            while f"{strategy_id}{TRANCHE_SEP}{n}" in account.positions:
                n += 1
            return f"{strategy_id}{TRANCHE_SEP}{n}"

        # ---- one cycle -------------------------------------------------------
        def run_cycle(self, *, now: datetime | None = None) -> dict:
            """Phases 1 / forecast / 2a are the production `run_cycle` (2a
            generalised to tranches); Phase 2b is the thesis auction."""
            ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
            account = self._load_account()
            self._migrate_exit_metadata(account)

            # Phase 1 (verbatim): gather each strategy's signal + price + risk basis.
            plans: list[dict] = []
            prices: dict[str, float] = {}
            errors: dict[str, str] = {}
            for sc in self._strategies:
                try:
                    engine = self._engines[sc.id]
                    required_timeframes = list(
                        dict.fromkeys([
                            sc.timeframe,
                            *getattr(engine, "required_timeframes", []),
                            *([sc.monitor_timeframe] if sc.monitor_timeframe else []),
                        ])
                    )
                    candles_by_timeframe = {
                        timeframe: self._provider(sc.symbol, timeframe, self._candles_limit)
                        for timeframe in required_timeframes
                    }
                    candles = candles_by_timeframe.get(sc.timeframe, [])
                    if not candles:
                        errors[sc.id] = "no candles"
                        continue
                    candle_ts = candles[-1].get("ts")
                    duplicate = account.processed_candles.get(sc.id) == candle_ts
                    monitor_candles = candles_by_timeframe.get(sc.monitor_timeframe, []) if sc.monitor_timeframe else []
                    mark_candle = monitor_candles[-1] if monitor_candles else candles[-1]
                    execution_price = float(candles[-1]["close"])
                    mark_price = float(mark_candle["close"])
                    prices[sc.symbol] = mark_price
                    signal = None
                    if not duplicate:
                        signal = engine.on_candle(candles[-1], StrategyContext(
                            candles=candles, symbol=sc.symbol, timeframe=sc.timeframe,
                            candles_by_timeframe=candles_by_timeframe,
                        ))
                    plans.append({"sc": sc, "execution_price": execution_price,
                                  "mark_price": mark_price, "atr_risk": _ATR_MULT * _atr(candles),
                                  "signal": signal, "candle_ts": candle_ts, "duplicate": duplicate,
                                  "monitor_candle": mark_candle if monitor_candles else None,
                                  "monitor_candles": monitor_candles})
                except Exception as exc:  # noqa: BLE001 - strict per-strategy isolation
                    errors[sc.id] = f"{type(exc).__name__}: {exc}"

            # Forecast refresh block (verbatim from production).
            try:
                persisted_forecast_state = json.loads(self._forecast_state_path.read_text())
            except (OSError, ValueError):
                persisted_forecast_state = {}
            forecast_state: dict[str, dict] = dict(persisted_forecast_state.get("assets") or {})
            forecast_state_by_strategy: dict[str, dict] = dict(
                persisted_forecast_state.get("strategies") or {}
            )
            forecast_history_records = self._read_records(self._forecast_history_path)
            prediction_keys = {
                (str(row.get("strategy_id")), str(row.get("bucket_ts")))
                for row in forecast_history_records if row.get("record_type") == "prediction"
            }
            if self._forecast_enabled:
                for plan in plans:
                    sc = plan["sc"]
                    try:
                        forecast_candles = self._provider(sc.symbol, "1h", self._forecast_history_limit)
                        forecast_report = self._forecast_evaluator(
                            forecast_candles,
                            min_evaluations=self._forecast_min_evaluations,
                            neighbour_count=self._forecast_neighbour_count,
                        )
                        gate_decision = decide_forecast_gate(
                            forecast_report,
                            atr_risk_fraction=(plan["atr_risk"] / plan["execution_price"]
                                               if plan["execution_price"] else 0.0),
                        )
                    except Exception as exc:  # noqa: BLE001 - fail open to baseline sizing
                        forecast_report = {"active": False, "lock_reasons": [f"forecast error: {type(exc).__name__}: {exc}"]}
                        gate_decision = {"action": "locked", "multiplier": 1.0, "influenced": False,
                                         "reason": forecast_report["lock_reasons"][0]}
                    plan["forecast_report"] = forecast_report
                    plan["gate_decision"] = gate_decision
                    state_row = {**forecast_report, "decision": gate_decision,
                                 "strategy_id": sc.id, "updated_at": ts}
                    forecast_state[sc.symbol] = state_row
                    forecast_state_by_strategy[sc.id] = state_row
                    prediction = prediction_record(
                        forecast_report,
                        strategy_id=sc.id,
                        symbol=sc.symbol,
                        decision=gate_decision,
                        recorded_at=ts,
                    )
                    if prediction is not None:
                        prediction_key = (sc.id, prediction["bucket_ts"])
                        if prediction_key not in prediction_keys:
                            self._append(self._forecast_history_path, prediction)
                            forecast_history_records.append(prediction)
                            prediction_keys.add(prediction_key)
                        realizations = due_realization_records(
                            forecast_history_records,
                            strategy_id=sc.id,
                            candles=forecast_candles,
                            recorded_at=ts,
                        )
                        for realization in realizations:
                            self._append(self._forecast_history_path, realization)
                            forecast_history_records.append(realization)

            # Phase 2a: protective exits, generalised over tranches. With a
            # single position per strategy this is the production loop.
            intents: dict[str, str] = {}
            fills: list[dict] = []
            protectively_closed: set[str] = set()
            for plan in plans:
                sc = plan["sc"]
                monitor_candles = plan.get("monitor_candles") or []
                if not monitor_candles:
                    continue
                for key in self._tranche_keys(account, sc.id):
                    position = account.positions.get(key)
                    if position is None:
                        continue
                    if position.last_monitor_candle_ts is None:
                        pending_monitor_candles = monitor_candles[-1:]
                    else:
                        try:
                            last_monitor_ts = float(position.last_monitor_candle_ts)
                            pending_monitor_candles = [
                                candle for candle in monitor_candles
                                if float(candle.get("ts")) > last_monitor_ts
                            ]
                        except (TypeError, ValueError):
                            pending_monitor_candles = monitor_candles[-1:]
                    for monitor_candle in pending_monitor_candles:
                        position.last_monitor_candle_ts = monitor_candle.get("ts")
                        protective = self._protective_exit(position, monitor_candle)
                        if protective is None:
                            continue
                        reason, fill_price = protective
                        fill = self._broker.close(
                            account, strategy_id=key, price=fill_price, ts=ts, reason=reason,
                        )
                        if fill:
                            intents[sc.id] = f"close_{reason}"
                            protectively_closed.add(sc.id)
                            fills.append(fill)
                            self._append(self._fills_path, fill)
                        break

            equity_for_sizing = account.equity(prices)

            # Phase 2b — THE AUCTION. Pass 1: resolve every non-entry intent
            # exactly like the baseline and collect the cycle's entry requests.
            requests: list[dict] = []
            auction: list[dict] = []
            for plan in plans:
                sc = plan["sc"]
                if sc.id in protectively_closed:
                    continue
                if plan["duplicate"]:
                    intents[sc.id] = "duplicate_candle"
                    continue
                signal = plan["signal"]
                side = signal.side if signal else None
                if side == Side.LONG:
                    if not sc.entry_enabled:
                        intents[sc.id] = "entry_disabled"
                        continue
                    account.processed_candles[sc.id] = plan["candle_ts"]
                    effective_risk_pct = sc.risk_pct
                    gate_decision = {"action": "disabled", "multiplier": 1.0,
                                     "influenced": False, "reason": "forecast gate disabled"}
                    forecast_report: dict = {"active": False, "lock_reasons": ["forecast gate disabled"]}
                    if self._forecast_enabled and not self._tranche_keys(account, sc.id):
                        forecast_report = plan["forecast_report"]
                        gate_decision = plan["gate_decision"]
                        effective_risk_pct *= gate_decision["multiplier"]
                        audit = {
                            "ts": ts, "strategy_id": sc.id, "symbol": sc.symbol,
                            "baseline_intent": "open", "baseline_risk_pct": sc.risk_pct,
                            "effective_risk_pct": effective_risk_pct,
                            **gate_decision, "forecast": forecast_report,
                        }
                        self._append(self._forecast_audit_path, audit)
                    if gate_decision["multiplier"] == 0:
                        intents[sc.id] = "forecast_veto"
                        continue
                    requests.append({"plan": plan, "effective_risk_pct": effective_risk_pct})
                elif side == Side.EXIT:
                    account.processed_candles[sc.id] = plan["candle_ts"]
                    closed_any = False
                    for key in self._tranche_keys(account, sc.id):
                        fill = self._broker.close(account, strategy_id=key,
                                                  price=plan["execution_price"],
                                                  ts=ts, reason=signal.entry_reason)
                        if fill:
                            closed_any = True
                            fills.append(fill)
                            self._append(self._fills_path, fill)
                    intents[sc.id] = "close" if closed_any else "no_trade"
                else:
                    account.processed_candles[sc.id] = plan["candle_ts"]
                    intents[sc.id] = "no_trade"

            # Pass 2: allocate the per-thesis budget in merit order. Budgets are
            # recomputed from open positions before each grant (same USD risk
            # accounting as the baseline caps), so a grant immediately shrinks
            # what the next intent can take.
            requests.sort(key=lambda req: self._merit_key[req["plan"]["sc"].id])
            for req in requests:
                plan = req["plan"]
                sc: StrategyConfig = plan["sc"]
                held = self._tranche_keys(account, sc.id)
                requested_usd = req["effective_risk_pct"] * equity_for_sizing
                open_total_risk_usd = sum(
                    position.qty * position.atr_risk
                    for position in account.positions.values()
                )
                open_symbol_risk_usd = sum(
                    position.qty * position.atr_risk
                    for position in account.positions.values()
                    if position.symbol == sc.symbol
                )
                remaining_total = self._max_total_stop_risk_pct * equity_for_sizing - open_total_risk_usd
                remaining_thesis = self._max_symbol_stop_risk_pct * equity_for_sizing - open_symbol_risk_usd
                record = {
                    "ts": ts, "strategy_id": sc.id, "symbol": sc.symbol, "direction": "long",
                    "requested_risk_usd": round(requested_usd, 6),
                    "remaining_thesis_usd": round(remaining_thesis, 6),
                    "remaining_total_usd": round(remaining_total, 6),
                    "equity_for_sizing": round(equity_for_sizing, 6),
                    "held_tranches": len(held),
                }
                if held and self._reentry_policy == "hold":
                    intents[sc.id] = "reentry_hold"
                    auction.append({**record, "granted_risk_usd": 0.0, "outcome": "reentry_hold"})
                    continue
                granted = min(requested_usd, remaining_thesis, remaining_total)
                floor = self._min_topup_fraction * requested_usd
                if granted <= 0 or granted < floor:
                    binding = "thesis" if remaining_thesis <= remaining_total else "total"
                    intents[sc.id] = ("thesis_already_funded" if binding == "thesis"
                                      else "risk_cap_total")
                    auction.append({**record, "granted_risk_usd": 0.0, "outcome": intents[sc.id]})
                    continue
                key = self._next_tranche_key(account, sc.id)
                signal = plan["signal"]
                fill = self._broker.open(
                    account, strategy_id=key, symbol=sc.symbol, price=plan["execution_price"],
                    atr_risk=plan["atr_risk"], risk_pct=granted / equity_for_sizing,
                    equity_for_sizing=equity_for_sizing, ts=ts, entry_reason=signal.entry_reason,
                    exit_policy=sc.exit_policy, monitor_timeframe=sc.monitor_timeframe,
                    stop_loss_price=signal.suggested_stop,
                    take_profit_price=signal.suggested_take_profit,
                    sl_basis="strategy_suggested" if signal.suggested_stop is not None else "",
                    reward_risk_ratio=sc.reward_risk_ratio,
                    max_leverage=self._max_leverage)
                if fill and key in account.positions and plan.get("monitor_candle") is not None:
                    account.positions[key].last_monitor_candle_ts = plan["monitor_candle"].get("ts")
                if fill:
                    fills.append(fill)
                    self._append(self._fills_path, fill)
                    outcome = "granted_full" if granted >= requested_usd else "granted_topup"
                    intents[sc.id] = "open" if not held else "open_topup"
                else:
                    outcome = "broker_noop"
                    intents[sc.id] = "hold"
                auction.append({**record,
                                "granted_risk_usd": round(granted if fill else 0.0, 6),
                                "grant_fraction": round(granted / requested_usd, 6) if requested_usd else None,
                                "tranche_key": key, "outcome": outcome})

            equity_after = account.equity(prices)
            self._save_account(account)
            if self._forecast_enabled and forecast_state_by_strategy:
                atomic_write_json(
                    self._forecast_state_path,
                    {"updated_at": ts, "assets": forecast_state,
                     "strategies": forecast_state_by_strategy},
                )

            per_strategy = {
                pos.strategy_id: round(pos.unrealized_usd(prices[pos.symbol]), 4)
                for pos in account.positions.values() if pos.symbol in prices
            }
            equity_record = {
                "ts": ts, "equity_usd": round(equity_after, 4),
                "balance_usd": round(account.balance_usd, 4),
                "open_positions": len(account.positions),
                "unrealized_by_strategy": per_strategy,
            }
            self._append(self._equity_path, equity_record)

            return {"ts": ts, "equity_usd": equity_after, "balance_usd": account.balance_usd,
                    "intents": intents, "fills": fills, "errors": errors,
                    "open_positions": list(account.positions), "auction": auction}

    return ThesisArbiterEngine


def run_arbiter_replay(portfolio_config: dict, provider: SnapshotProvider, *,
                       run_dir: Path, start_ms: int, end_ms: int,
                       event_log: EventLog | None = None,
                       gate: str = "exact",
                       reentry_policy: str = "hold",
                       merit_order: list[str] | None = None,
                       min_topup_fraction: float = 0.0) -> dict:
    """Drive ThesisArbiterEngine cycle by cycle. Mirrors run_runtime_replay:
    every persistence path stays inside run_dir; the gate memoization and
    exact/fast selection are identical."""
    from orum.portfolio.forecast_gate import walk_forward_forecast
    from scripts.replay_harness.runtime_replay import _memoized_forecast

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
    engine_cls = make_arbiter_engine_class()
    engine = engine_cls(
        portfolio_config,
        candle_provider=provider.bound_provider(clock),
        positions_path=paths["positions"],
        fills_path=jsonl["fills"],
        equity_path=jsonl["equity"],
        forecast_state_path=paths["forecast_state"],
        forecast_audit_path=jsonl["forecast_audit"],
        forecast_history_path=jsonl["forecast_history"],
        forecast_evaluator=_memoized_forecast(evaluator),
        reentry_policy=reentry_policy,
        merit_order=merit_order,
        min_topup_fraction=min_topup_fraction,
    )

    summaries: list[dict] = []
    for now_ms in cycle_times(start_ms, end_ms):
        clock.now_ms = now_ms
        summary = engine.run_cycle(now=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc))
        summaries.append(summary)
        if event_log:
            for sid, intent in summary["intents"].items():
                if intent not in ("no_trade", "duplicate_candle"):
                    event_log.emit("portfolio_decision", baseline="thesis_arbiter",
                                   cycle_observed_time=now_ms, strategy_id=sid, intent=intent)
            for record in summary.get("auction", []):
                event_log.emit("auction", baseline="thesis_arbiter",
                               cycle_observed_time=now_ms, **record)
            for fill in summary["fills"]:
                event_log.emit("fill", baseline="thesis_arbiter",
                               cycle_observed_time=now_ms, **fill)
            for sid, err in summary.get("errors", {}).items():
                event_log.emit("strategy_error", baseline="thesis_arbiter",
                               cycle_observed_time=now_ms, strategy_id=sid, error=err)

    (run_dir / "runtime_summaries.json").write_text(json.dumps(summaries, indent=1, default=str))
    return {"cycles": len(summaries), "ledger_dir": str(ledger_dir),
            "arbiter": {"reentry_policy": reentry_policy, "merit_order": merit_order,
                        "min_topup_fraction": min_topup_fraction},
            "final": summaries[-1] if summaries else None}
