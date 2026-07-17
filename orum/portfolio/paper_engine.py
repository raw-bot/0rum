"""Paper portfolio engine — orchestrates every strategy against ONE shared
account and writes the unified ledger.

This replaces the old "mono-asset worker + separate portfolio_shadow" split.
Each configured strategy runs independently every cycle; the shared `Account`
(in `paper_broker`) is the only place fills, positions, cash and equity live.

Isolation is a hard requirement: one strategy raising, returning no data, or
(for gold_cot) finding its COT cache missing must NEVER stop the others. Every
per-strategy step is wrapped, and a failure is recorded as an error for that
strategy alone.

The candle provider is injected (`candle_provider(symbol, timeframe, limit)`),
so the whole cycle is unit-testable offline with synthetic data — no network in
the tests, and none is hard-wired here.

Config (the `portfolio` block of goal.yaml):
    portfolio:
      starting_balance_usd: 10000
      candles_limit: 300
      strategies:
        - id: btc_ak_macd
          engine: ak_macd        # registry name
          symbol: BTC/USDT
          timeframe: "4h"
          risk_pct: 0.02
          params: {}
        - id: eth_donchian
          engine: donchian
          symbol: ETH/USDT
          timeframe: "1d"
          risk_pct: 0.02
          params: {entry_n: 20, exit_n: 10}
        - id: gold_cot
          engine: gold_cot
          symbol: PAXG/USDT
          timeframe: "1d"
          risk_pct: 0.02
          params: {}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from orum.fsio import atomic_write_json
from orum.paths import (
    FORECAST_AUDIT_PATH,
    FORECAST_GATE_PATH,
    FORECAST_HISTORY_PATH,
    PAPER_EQUITY_PATH,
    PAPER_FILLS_PATH,
    PAPER_POSITIONS_PATH,
)
from orum.portfolio.forecast_gate import decide_forecast_gate, walk_forward_forecast
from orum.portfolio.forecast_history import due_realization_records, prediction_record
from orum.portfolio.paper_broker import Account, PaperBroker
from orum.strategies import load_engine
from orum.strategies.base import Side, StrategyContext

CandleProvider = Callable[[str, str, int], list[dict]]
ForecastEvaluator = Callable[..., dict]

_ATR_LEN = 14
_ATR_MULT = 2.0  # atr_risk = 2*ATR, same basis as the validated shadow model


def _atr(candles: list[dict], n: int = _ATR_LEN) -> float:
    """Wilder ATR of the closed candles; 0.0 if there is not enough history."""
    if len(candles) < n + 1:
        return 0.0
    h = [c["high"] for c in candles]
    l = [c["low"] for c in candles]
    c = [c["close"] for c in candles]
    tr = [h[0] - l[0]]
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    a = 1 / n
    atr = tr[0]
    for x in tr[1:]:
        atr = a * x + (1 - a) * atr
    return atr


@dataclass
class StrategyConfig:
    id: str
    engine: str
    symbol: str
    timeframe: str
    risk_pct: float
    entry_enabled: bool
    exit_policy: str
    monitor_timeframe: str | None
    reward_risk_ratio: float | None
    params: dict


def _parse_strategies(config: dict) -> list[StrategyConfig]:
    out: list[StrategyConfig] = []
    for raw in config.get("strategies") or []:
        entry_enabled = raw.get("entry_enabled", True)
        if not isinstance(entry_enabled, bool):
            raise ValueError("entry_enabled must be boolean")
        engine = str(raw["engine"])
        default_policy = {
            "ak_macd": "structural_bracket",
            "utbot_mtf": "signal_or_stop",
            "donchian": "donchian_signal",
            "gold_cot": "cot_signal",
            "ha_trend": "structural_bracket",
        }.get(engine, "strategy_signal")
        exit_policy = str(raw.get("exit_policy") or default_policy)
        default_monitor = "15m" if engine == "ak_macd" else raw.get("timeframe", "1d")
        monitor_timeframe = raw.get("monitor_timeframe", default_monitor)
        reward_risk_ratio = raw.get("reward_risk_ratio", 1.5 if engine == "ak_macd" else None)
        out.append(
            StrategyConfig(
                id=str(raw["id"]),
                engine=engine,
                symbol=str(raw["symbol"]),
                timeframe=str(raw.get("timeframe", "1d")),
                risk_pct=float(raw.get("risk_pct", 0.02)),
                entry_enabled=entry_enabled,
                exit_policy=exit_policy,
                monitor_timeframe=str(monitor_timeframe) if monitor_timeframe else None,
                reward_risk_ratio=(float(reward_risk_ratio) if reward_risk_ratio is not None else None),
                params=dict(raw.get("params") or {}),
            )
        )
    return out


class PaperEngine:
    def __init__(
        self,
        config: dict,
        *,
        candle_provider: CandleProvider,
        broker: PaperBroker | None = None,
        positions_path: Path = PAPER_POSITIONS_PATH,
        fills_path: Path = PAPER_FILLS_PATH,
        equity_path: Path = PAPER_EQUITY_PATH,
        forecast_state_path: Path = FORECAST_GATE_PATH,
        forecast_audit_path: Path = FORECAST_AUDIT_PATH,
        forecast_history_path: Path = FORECAST_HISTORY_PATH,
        forecast_evaluator: ForecastEvaluator = walk_forward_forecast,
    ) -> None:
        self._config = config or {}
        self._provider = candle_provider
        self._broker = broker or PaperBroker()
        self._positions_path = Path(positions_path)
        self._fills_path = Path(fills_path)
        self._equity_path = Path(equity_path)
        self._forecast_state_path = Path(forecast_state_path)
        self._forecast_audit_path = Path(forecast_audit_path)
        self._forecast_history_path = Path(forecast_history_path)
        self._forecast_evaluator = forecast_evaluator
        self._candles_limit = int(self._config.get("candles_limit", 300))
        self._starting_balance = float(self._config.get("starting_balance_usd", 10_000.0))
        self._max_total_stop_risk_pct = float(
            self._config.get("max_total_stop_risk_pct", 1.0)
        )
        self._max_symbol_stop_risk_pct = float(
            self._config.get("max_symbol_stop_risk_pct", 1.0)
        )
        # Optional notional cap (× equity). Absent/None keeps the historical
        # behaviour byte-for-byte: pure risk-based sizing, no cap.
        raw_leverage = self._config.get("max_leverage")
        self._max_leverage = float(raw_leverage) if raw_leverage else None
        forecast_config = dict(self._config.get("forecast_gate") or {})
        self._forecast_enabled = forecast_config.get("enabled", False) is True
        self._forecast_history_limit = int(forecast_config.get("history_limit", 900))
        self._forecast_min_evaluations = int(forecast_config.get("min_evaluations", 250))
        self._forecast_neighbour_count = int(forecast_config.get("neighbour_count", 80))
        self._strategies = _parse_strategies(self._config)
        # Engines are built once (init reads params); on_candle is called per cycle.
        self._engines = {sc.id: load_engine({"strategy_engine": {"name": sc.engine, "params": sc.params}})
                         for sc in self._strategies}

    # ---- persistence -----------------------------------------------------
    def _load_account(self) -> Account:
        try:
            data = json.loads(self._positions_path.read_text())
        except (OSError, ValueError):
            data = None
        return Account.from_dict(data, starting_balance=self._starting_balance)

    def _save_account(self, account: Account) -> None:
        payload = account.to_dict()
        payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_write_json(self._positions_path, payload)

    def _append(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    @staticmethod
    def _read_records(path: Path) -> list[dict]:
        try:
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        except (OSError, ValueError):
            return []

    def _migrate_exit_metadata(self, account: Account) -> None:
        """Backfill policy metadata without changing size, entry or balance.

        The open AK trade predates persisted structural inputs. Its `atr_risk`
        is therefore the only honest frozen risk distance available.
        """
        configs = {strategy.id: strategy for strategy in self._strategies}
        for strategy_id, position in account.positions.items():
            strategy = configs.get(strategy_id)
            if strategy is None:
                continue
            position.exit_policy = strategy.exit_policy
            position.monitor_timeframe = strategy.monitor_timeframe
            if strategy.reward_risk_ratio is not None:
                position.reward_risk_ratio = strategy.reward_risk_ratio
            if (
                strategy.exit_policy == "structural_bracket"
                and (position.stop_loss_price is None or position.take_profit_price is None)
                and position.atr_risk > 0
            ):
                reward_risk = strategy.reward_risk_ratio or 1.5
                position.stop_loss_price = position.entry_px - position.atr_risk
                position.take_profit_price = position.entry_px + reward_risk * position.atr_risk
                position.sl_basis = "atr_fallback_migration"

    @staticmethod
    def _protective_exit(position, candle: dict) -> tuple[str, float] | None:
        """Return reason/frozen fill level. Same-candle collisions are SL-first."""
        if position.side != "long":
            return None
        low = float(candle.get("low", candle.get("close", 0.0)))
        high = float(candle.get("high", candle.get("close", 0.0)))
        if position.stop_loss_price is not None and low <= position.stop_loss_price:
            return "stop_loss", float(position.stop_loss_price)
        if position.take_profit_price is not None and high >= position.take_profit_price:
            return "take_profit", float(position.take_profit_price)
        return None

    # ---- one cycle -------------------------------------------------------
    def run_cycle(self, *, now: datetime | None = None) -> dict:
        """Evaluate every strategy once and apply intents to the shared account.
        Returns a summary; never raises because of a single strategy."""
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        account = self._load_account()
        self._migrate_exit_metadata(account)

        # Phase 1: gather each strategy's signal + price + risk basis. A failure
        # here is contained to that strategy so the others still trade.
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

        # Refresh the forecast on every healthy market cycle, not only when a
        # strategy happens to emit LONG.  This keeps calibration and the UI
        # current; influence is still applied only to a genuine new entry below.
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
                sc: StrategyConfig = plan["sc"]
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

        # Phase 2a: protective levels are independent from primary strategy
        # deduplication. An H4 strategy can therefore exit on a fresh closed M15
        # candle even when its H4 signal candle has not changed.
        intents: dict[str, str] = {}
        fills: list[dict] = []
        protectively_closed: set[str] = set()
        for plan in plans:
            sc: StrategyConfig = plan["sc"]
            position = account.positions.get(sc.id)
            monitor_candles = plan.get("monitor_candles") or []
            if position is None or not monitor_candles:
                continue
            if position.last_monitor_candle_ts is None:
                # A migrated position has no historical monitoring cursor. Do
                # not retroactively close it on a candle from before this
                # policy existed; establish the cursor at the latest close.
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
                    account, strategy_id=sc.id, price=fill_price, ts=ts, reason=reason,
                )
                if fill:
                    intents[sc.id] = f"close_{reason}"
                    protectively_closed.add(sc.id)
                    fills.append(fill)
                    self._append(self._fills_path, fill)
                break

        # Equity snapshot AFTER protective closes and BEFORE new entries. Every
        # strategy still sizes from the same post-exit account value.
        equity_for_sizing = account.equity(prices)

        # Phase 2b: turn fresh strategy intents into fills on the shared account.
        for plan in plans:
            sc: StrategyConfig = plan["sc"]
            if sc.id in protectively_closed:
                continue
            if plan["duplicate"]:
                intents[sc.id] = "duplicate_candle"
                continue
            signal = plan["signal"]
            side = signal.side if signal else None
            if side == Side.LONG:
                if not sc.entry_enabled:
                    fill = None
                    intents[sc.id] = "entry_disabled"
                else:
                    account.processed_candles[sc.id] = plan["candle_ts"]
                    effective_risk_pct = sc.risk_pct
                    gate_decision = {"action": "disabled", "multiplier": 1.0,
                                     "influenced": False, "reason": "forecast gate disabled"}
                    forecast_report: dict = {"active": False, "lock_reasons": ["forecast gate disabled"]}
                    if self._forecast_enabled and sc.id not in account.positions:
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
                        fill = None
                        intents[sc.id] = "forecast_veto"
                        continue
                    open_total_risk_usd = sum(
                        position.qty * position.atr_risk
                        for position in account.positions.values()
                    )
                    open_symbol_risk_usd = sum(
                        position.qty * position.atr_risk
                        for position in account.positions.values()
                        if position.symbol == sc.symbol
                    )
                    candidate_risk_usd = effective_risk_pct * equity_for_sizing
                    if (
                        open_total_risk_usd + candidate_risk_usd
                        > self._max_total_stop_risk_pct * equity_for_sizing
                    ):
                        fill = None
                        intents[sc.id] = "risk_cap_total"
                        continue
                    if (
                        open_symbol_risk_usd + candidate_risk_usd
                        > self._max_symbol_stop_risk_pct * equity_for_sizing
                    ):
                        fill = None
                        intents[sc.id] = "risk_cap_symbol"
                        continue
                    fill = self._broker.open(
                        account, strategy_id=sc.id, symbol=sc.symbol, price=plan["execution_price"],
                        atr_risk=plan["atr_risk"], risk_pct=effective_risk_pct,
                        equity_for_sizing=equity_for_sizing, ts=ts, entry_reason=signal.entry_reason,
                        exit_policy=sc.exit_policy, monitor_timeframe=sc.monitor_timeframe,
                        stop_loss_price=signal.suggested_stop,
                        take_profit_price=signal.suggested_take_profit,
                        sl_basis="strategy_suggested" if signal.suggested_stop is not None else "",
                        reward_risk_ratio=sc.reward_risk_ratio,
                        max_leverage=self._max_leverage)
                    if fill and sc.id in account.positions and plan.get("monitor_candle") is not None:
                        account.positions[sc.id].last_monitor_candle_ts = plan["monitor_candle"].get("ts")
                    intents[sc.id] = "open" if fill else "hold"
            elif side == Side.EXIT:
                account.processed_candles[sc.id] = plan["candle_ts"]
                fill = self._broker.close(account, strategy_id=sc.id, price=plan["execution_price"],
                                          ts=ts, reason=signal.entry_reason)
                intents[sc.id] = "close" if fill else "no_trade"
            else:
                account.processed_candles[sc.id] = plan["candle_ts"]
                intents[sc.id] = "no_trade"
                fill = None
            if fill:
                fills.append(fill)
                self._append(self._fills_path, fill)

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
                "open_positions": list(account.positions)}
