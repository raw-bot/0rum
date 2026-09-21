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
import hashlib
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Callable

from orum.fsio import atomic_write_json
from orum.paths import (
    PAPER_EQUITY_PATH,
    PAPER_FILLS_PATH,
    PAPER_POSITIONS_PATH,
)
from orum.portfolio.dynamic_risk import DynamicRiskShadow
from orum.portfolio.paper_broker import Account, PaperBroker
from orum.portfolio.position_manager import (
    DynamicExitPolicy,
    DynamicExitState,
    ExitAction,
    evaluate_dynamic_exit,
)
from orum.strategies import load_engine
from orum.strategies.base import Side, StrategyContext

CandleProvider = Callable[[str, str, int], list[dict]]

_ATR_LEN = 14
_ATR_MULT = 2.0  # atr_risk = 2*ATR, same basis as the validated shadow model
TRANCHE_SEP = "::t"

_TIMEFRAME_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


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


def _ema_last(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
    return current


def _entry_risk_distance(
    entry_price: float,
    stop_loss_price: float | None,
    atr_risk: float,
    *,
    side: str = "long",
) -> float | None:
    """Freeze the actual entry-to-stop distance.

    The active paper portfolio uses it for sizing and caps; historical configs
    can still select the legacy ATR basis. A malformed explicit stop fails
    closed before an entry can open.
    """
    if side not in ("long", "short"):
        return None
    if stop_loss_price is None:
        return atr_risk if isfinite(atr_risk) and atr_risk > 0 else None
    try:
        entry = float(entry_price)
        stop = float(stop_loss_price)
    except (TypeError, ValueError):
        return None
    if not isfinite(entry) or not isfinite(stop):
        return None
    direction = 1.0 if side == "long" else -1.0
    distance = direction * (entry - stop)
    return distance if isfinite(distance) and distance > 0 else None


def _valid_short_bracket(
    entry_price: float,
    stop_loss_price: float | None,
    take_profit_price: float | None,
) -> bool:
    if stop_loss_price is None or take_profit_price is None:
        return False
    try:
        stop = float(stop_loss_price)
        target = float(take_profit_price)
    except (TypeError, ValueError):
        return False
    return (
        isfinite(stop)
        and isfinite(target)
        and stop > entry_price > target > 0
    )


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
    dynamic_exit: DynamicExitPolicy | None
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
        if reward_risk_ratio is not None:
            if isinstance(reward_risk_ratio, bool):
                raise ValueError("reward_risk_ratio must be finite and positive")
            reward_risk_ratio = float(reward_risk_ratio)
            if not isfinite(reward_risk_ratio) or reward_risk_ratio <= 0:
                raise ValueError("reward_risk_ratio must be finite and positive")
        dynamic_exit = DynamicExitPolicy.from_config(raw.get("dynamic_exit"))
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
                reward_risk_ratio=reward_risk_ratio,
                dynamic_exit=dynamic_exit,
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
        dynamic_exit_path: Path | None = None,
        shadow_regime_path: Path | None = None,
        dynamic_risk_shadow_path: Path | None = None,
        valuation_marks: dict[str, float] | None = None,
    ) -> None:
        self._config = config or {}
        self._provider = candle_provider
        # Optional common valuation for isolated research accounts. Execution and
        # protective prices still come from each strategy's native monitor bars.
        self._valuation_marks = dict(valuation_marks) if valuation_marks is not None else None
        if self._valuation_marks is not None:
            if config.get("execution_mode") != "observed_mark" or config.get("reentry_policy") is None:
                raise ValueError("common valuation requires the observed_mark auction")
            if any(not isfinite(value) or value <= 0 for value in self._valuation_marks.values()):
                raise ValueError("invalid common valuation mark")
        self._broker = broker or PaperBroker()
        self._positions_path = Path(positions_path)
        self._fills_path = Path(fills_path)
        self._equity_path = Path(equity_path)
        self._dynamic_exit_path = Path(
            dynamic_exit_path or self._fills_path.with_name("paper_dynamic_exits.jsonl")
        )
        self._shadow_regime_path = Path(
            shadow_regime_path
            or self._equity_path.with_name("paper_regime_shadow.jsonl")
        )
        self._dynamic_risk_shadow_path = Path(
            dynamic_risk_shadow_path
            or self._equity_path.with_name("paper_dynamic_risk_shadow.jsonl")
        )
        self._dynamic_decision_ids: set[str] | None = None
        self._candles_limit = int(self._config.get("candles_limit", 300))
        self._starting_balance = float(self._config.get("starting_balance_usd", 10_000.0))
        self._max_total_stop_risk_pct = float(
            self._config.get("max_total_stop_risk_pct", 1.0)
        )
        self._max_symbol_stop_risk_pct = float(
            self._config.get("max_symbol_stop_risk_pct", 1.0)
        )
        self._risk_sizing_basis = str(
            self._config.get("risk_sizing_basis", "atr")
        )
        if self._risk_sizing_basis not in ("atr", "actual_stop"):
            raise ValueError(
                f"unknown risk_sizing_basis {self._risk_sizing_basis!r}"
            )
        self._execution_mode = str(
            self._config.get("execution_mode", "signal_close")
        )
        if self._execution_mode not in ("signal_close", "next_open", "observed_mark"):
            raise ValueError(f"unknown execution_mode {self._execution_mode!r}")
        raw_sides = self._config.get(
            "allowed_entry_sides", ["long", "short"]
        )
        if (
            not isinstance(raw_sides, list)
            or not raw_sides
            or any(side not in ("long", "short") for side in raw_sides)
        ):
            raise ValueError("allowed_entry_sides must contain long and/or short")
        self._allowed_entry_sides = frozenset(raw_sides)
        raw_drawdown = self._config.get("entry_drawdown_kill_pct")
        self._entry_drawdown_kill_pct = (
            float(raw_drawdown) if raw_drawdown is not None else None
        )
        if (
            self._entry_drawdown_kill_pct is not None
            and not 0 < self._entry_drawdown_kill_pct < 1
        ):
            raise ValueError("entry_drawdown_kill_pct must be between 0 and 1")
        raw_drawdown_scale = self._config.get("entry_drawdown_risk_scale")
        if raw_drawdown_scale is not None and self._entry_drawdown_kill_pct is not None:
            raise ValueError(
                "entry_drawdown_kill_pct and entry_drawdown_risk_scale are mutually exclusive"
            )
        self._entry_drawdown_risk_scale = None
        if raw_drawdown_scale is not None:
            if not isinstance(raw_drawdown_scale, dict):
                raise ValueError("entry_drawdown_risk_scale must be a mapping")
            try:
                start_pct = float(raw_drawdown_scale["start_pct"])
                halt_pct = float(raw_drawdown_scale["halt_pct"])
                floor_multiplier = float(raw_drawdown_scale["floor_multiplier"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("invalid entry_drawdown_risk_scale") from None
            if not all(isfinite(value) for value in (
                start_pct, halt_pct, floor_multiplier
            )) or not (0 <= start_pct < halt_pct < 1) or not (
                0 < floor_multiplier <= 1
            ):
                raise ValueError("invalid entry_drawdown_risk_scale")
            self._entry_drawdown_risk_scale = {
                "start_pct": start_pct,
                "halt_pct": halt_pct,
                "floor_multiplier": floor_multiplier,
            }
        raw_dynamic_risk = self._config.get("dynamic_risk_shadow")
        self._dynamic_risk_shadow = None
        if raw_dynamic_risk is not None:
            if not isinstance(raw_dynamic_risk, dict):
                raise ValueError("dynamic_risk_shadow must be a mapping")
            if raw_dynamic_risk.get("enabled", False):
                artifact_path = Path(str(raw_dynamic_risk.get("artifact_path", "")))
                if not artifact_path.is_absolute():
                    artifact_path = Path(__file__).resolve().parents[2] / artifact_path
                self._dynamic_risk_shadow = DynamicRiskShadow.from_path(artifact_path)
        self._max_open_positions_by_symbol = {
            str(symbol): int(limit)
            for symbol, limit in (
                self._config.get("max_open_positions_by_symbol") or {}
            ).items()
        }
        if any(
            limit <= 0
            for limit in self._max_open_positions_by_symbol.values()
        ):
            raise ValueError("max_open_positions_by_symbol limits must be positive")
        self._max_symbol_notional_pct = {
            str(symbol): float(limit)
            for symbol, limit in (
                self._config.get("max_symbol_notional_pct") or {}
            ).items()
        }
        if any(
            not isfinite(limit) or limit <= 0
            for limit in self._max_symbol_notional_pct.values()
        ):
            raise ValueError("max_symbol_notional_pct limits must be positive")
        self._shadow_regime_filters = {}
        for symbol, raw_filter in (
            self._config.get("shadow_regime_filters") or {}
        ).items():
            if not isinstance(raw_filter, dict):
                raise ValueError("shadow_regime_filters entries must be mappings")
            timeframe = str(raw_filter.get("timeframe", "4h"))
            ema_period = int(raw_filter.get("ema_period", 200))
            enforce = raw_filter.get("enforce", False)
            if timeframe not in _TIMEFRAME_MS or ema_period <= 0:
                raise ValueError("invalid shadow regime filter")
            if enforce is not False:
                raise ValueError("shadow regime filters cannot be enforced")
            self._shadow_regime_filters[str(symbol)] = {
                "timeframe": timeframe,
                "ema_period": ema_period,
                "enforce": False,
            }
        # Optional notional cap (× equity). Absent/None keeps the historical
        # behaviour byte-for-byte: pure risk-based sizing, no cap.
        raw_leverage = self._config.get("max_leverage")
        self._max_leverage = float(raw_leverage) if raw_leverage else None
        # The forecast gate was removed on 2026-07-17 after the bounded OOS
        # study (backtests/reports/chantier3_forecast_gate.md): its KNN
        # quantiles underperformed climatology at every horizon. A leftover
        # `forecast_gate:` config block is ignored on purpose.
        self._strategies = _parse_strategies(self._config)
        self._reentry_policy = self._config.get("reentry_policy")
        if self._reentry_policy not in (None, "hold", "topup"):
            raise ValueError(f"unknown reentry_policy {self._reentry_policy!r}")
        if self._reentry_policy is None and any(
            strategy.dynamic_exit is not None for strategy in self._strategies
        ):
            raise ValueError("dynamic_exit requires the active auction portfolio path")
        self._min_topup_fraction = float(
            self._config.get("min_topup_fraction", 0.0)
        )
        merit_order = list(self._config.get("merit_order") or [])
        configured = {strategy.id for strategy in self._strategies}
        unknown = set(merit_order) - configured
        if unknown:
            raise ValueError(
                f"merit_order references unknown strategies: {sorted(unknown)}"
            )
        config_rank = {
            strategy.id: index for index, strategy in enumerate(self._strategies)
        }
        merit_rank = {strategy_id: index for index, strategy_id in enumerate(merit_order)}
        self._merit_key = {
            strategy.id: (
                merit_rank.get(strategy.id, len(merit_rank)),
                config_rank[strategy.id],
            )
            for strategy in self._strategies
        }
        # Engines are built once (init reads params); on_candle is called per cycle.
        self._engines = {}
        for sc in self._strategies:
            params = dict(sc.params)
            if sc.engine == "ak_macd" and sc.reward_risk_ratio is not None:
                params["reward_risk_ratio"] = sc.reward_risk_ratio
            self._engines[sc.id] = load_engine({
                "strategy_engine": {"name": sc.engine, "params": params}
            })

    # ---- persistence -----------------------------------------------------
    def _load_account(self) -> Account:
        if not self._positions_path.exists():
            return Account(balance_usd=self._starting_balance)
        try:
            data = json.loads(self._positions_path.read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"paper account state is unreadable: {self._positions_path}"
            ) from exc
        if not isinstance(data, dict) or "balance_usd" not in data:
            raise RuntimeError("paper account state has an invalid root schema")
        try:
            balance = float(data["balance_usd"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("paper account balance is invalid") from exc
        if not isfinite(balance):
            raise RuntimeError("paper account balance is not finite")
        positions = data.get("positions", {})
        if not isinstance(positions, dict) or any(
            not isinstance(value, dict) for value in positions.values()
        ):
            raise RuntimeError("paper account positions schema is invalid")
        processed = data.get("processed_candles", {})
        if not isinstance(processed, dict):
            raise RuntimeError("paper account candle cursor schema is invalid")
        pending = data.get("pending_fills", [])
        if not isinstance(pending, list) or any(
            not isinstance(value, dict) or not value.get("fill_id")
            for value in pending
        ):
            raise RuntimeError("paper account pending fill schema is invalid")
        return Account.from_dict(data, starting_balance=self._starting_balance)

    def _save_account(self, account: Account) -> None:
        payload = account.to_dict()
        payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        atomic_write_json(self._positions_path, payload)

    def _append(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def _entry_drawdown(self, current_equity: float) -> float:
        peak = max(self._starting_balance, current_equity)
        try:
            lines = self._equity_path.read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                value = float(json.loads(line).get("equity_usd"))
            except (AttributeError, TypeError, ValueError):
                continue
            if isfinite(value):
                peak = max(peak, value)
        return max(0.0, (peak - current_equity) / peak) if peak > 0 else 0.0

    def _entry_drawdown_multiplier(self, drawdown: float) -> float:
        if self._entry_drawdown_risk_scale is None:
            if (
                self._entry_drawdown_kill_pct is not None
                and drawdown >= self._entry_drawdown_kill_pct
            ):
                return 0.0
            return 1.0
        start = self._entry_drawdown_risk_scale["start_pct"]
        halt = self._entry_drawdown_risk_scale["halt_pct"]
        floor = self._entry_drawdown_risk_scale["floor_multiplier"]
        if drawdown < start:
            return 1.0
        if drawdown >= halt:
            return 0.0
        progress = (drawdown - start) / (halt - start)
        return 1.0 - progress * (1.0 - floor)

    def _shadow_regime(self, plan: dict, direction: str) -> dict | None:
        sc: StrategyConfig = plan["sc"]
        config = self._shadow_regime_filters.get(sc.symbol)
        if config is None:
            return None
        timeframe = config["timeframe"]
        candles = plan["signal_candles_by_timeframe"].get(timeframe, [])
        closes = [float(candle["close"]) for candle in candles]
        ema = _ema_last(closes, config["ema_period"])
        if ema is None:
            return {
                "timeframe": timeframe,
                "ema_period": config["ema_period"],
                "passed": None,
                "reason": "insufficient_history",
                "enforced": False,
            }
        close = closes[-1]
        passed = close > ema if direction == "long" else close < ema
        return {
            "timeframe": timeframe,
            "ema_period": config["ema_period"],
            "close": close,
            "ema": ema,
            "passed": passed,
            "reason": "above_ema" if close > ema else "below_or_equal_ema",
            "enforced": False,
        }

    @staticmethod
    def _repair_jsonl_tail(path: Path) -> None:
        """Remove only a torn final record left by a process crash."""
        try:
            data = path.read_bytes()
        except OSError:
            return
        if not data or data.endswith(b"\n"):
            return
        tail_start = data.rfind(b"\n") + 1
        try:
            json.loads(data[tail_start:])
        except (UnicodeDecodeError, ValueError):
            with path.open("r+b") as handle:
                handle.truncate(tail_start)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            with path.open("ab") as handle:
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())

    @staticmethod
    def _record_ids(path: Path, key: str) -> set[str]:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return set()
        found: set[str] = set()
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get(key):
                found.add(str(value[key]))
        return found

    @staticmethod
    def _append_durable(path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _flush_pending_fills(self, account: Account) -> None:
        """Publish account-authoritative fills exactly once, then clear outbox."""
        if not account.pending_fills:
            return
        self._repair_jsonl_tail(self._fills_path)
        committed = self._record_ids(self._fills_path, "fill_id")
        for fill in account.pending_fills:
            fill_id = str(fill["fill_id"])
            if fill_id not in committed:
                self._append_durable(self._fills_path, fill)
                committed.add(fill_id)
        account.pending_fills.clear()
        self._save_account(account)

    def _commit_fills(self, account: Account, fills: list[dict]) -> None:
        """Commit one cycle's account mutation and fills through one outbox."""
        for fill in fills:
            fill.setdefault("execution_method", self._execution_mode + "_v2")
            fill.setdefault(
                "fill_id",
                self._dynamic_id(
                    "fill",
                    fill.get("action"),
                    fill.get("position_id"),
                    fill.get("ts"),
                    fill.get("reason"),
                    fill.get("price"),
                ),
            )
            account.pending_fills.append(dict(fill))
        self._save_account(account)
        self._flush_pending_fills(account)

    def _record_dynamic_decision(self, record: dict) -> None:
        if self._dynamic_decision_ids is None:
            self._repair_jsonl_tail(self._dynamic_exit_path)
            self._dynamic_decision_ids = self._record_ids(
                self._dynamic_exit_path, "decision_id"
            )
        decision_id = str(record["decision_id"])
        if decision_id not in self._dynamic_decision_ids:
            self._append_durable(self._dynamic_exit_path, record)
            self._dynamic_decision_ids.add(decision_id)

    @staticmethod
    def _dynamic_id(*parts: object) -> str:
        material = "|".join(str(part) for part in parts).encode()
        return hashlib.sha256(material).hexdigest()[:24]

    @staticmethod
    def _monitor_history_has_gap(
        candles: list[dict], *, cursor: object, timeframe: str | None
    ) -> bool:
        interval = _TIMEFRAME_MS.get(str(timeframe))
        if interval is None or not candles:
            return True
        try:
            timestamps = [float(candle["ts"]) for candle in candles]
            cursor_ts = float(cursor)
        except (KeyError, TypeError, ValueError):
            return True
        relevant = [timestamp for timestamp in timestamps if timestamp > cursor_ts]
        if not relevant:
            return False
        previous = cursor_ts
        for timestamp in relevant:
            delta = timestamp - previous
            # Tests and hand-built fixtures use unit-spaced integer timestamps;
            # production epochs use milliseconds.  Small positive deltas are
            # contiguous fixtures, while a production hole exceeds one bar.
            if delta <= 0 or delta > interval * 1.5:
                return True
            previous = timestamp
        return False

    def _migrate_exit_metadata(self, account: Account) -> None:
        """Backfill policy metadata without changing size, entry or balance.

        The open AK trade predates persisted structural inputs. Its `atr_risk`
        is therefore the only honest frozen risk distance available.
        """
        configs = {strategy.id: strategy for strategy in self._strategies}
        for position in account.positions.values():
            strategy = configs.get(position.strategy_id)
            if strategy is None:
                continue
            position.exit_policy = strategy.exit_policy
            position.monitor_timeframe = strategy.monitor_timeframe
            if (
                position.take_profit_price is not None
                and position.risk_distance > 0
            ):
                direction = 1.0 if position.side == "long" else -1.0
                position.reward_risk_ratio = round(
                    direction
                    * (position.take_profit_price - position.entry_px)
                    / position.risk_distance,
                    12,
                )
            elif position.reward_risk_ratio is None:
                position.reward_risk_ratio = strategy.reward_risk_ratio
            if (
                strategy.exit_policy == "structural_bracket"
                and (position.stop_loss_price is None or position.take_profit_price is None)
                and position.atr_risk > 0
            ):
                reward_risk = strategy.reward_risk_ratio or 1.5
                direction = 1.0 if position.side == "long" else -1.0
                position.stop_loss_price = (
                    position.entry_px - direction * position.atr_risk
                )
                position.take_profit_price = (
                    position.entry_px
                    + direction * reward_risk * position.atr_risk
                )
                position.sl_basis = "atr_fallback_migration"

    @staticmethod
    def _gap_stop_price(position, stop: float, candle: dict) -> float:
        opn = float(candle.get("open", stop))
        if not isfinite(opn) or opn <= 0:
            raise ValueError("invalid monitor open")
        return min(stop, opn) if position.side == "long" else max(stop, opn)

    @staticmethod
    def _static_protective_exit(position, candle: dict) -> tuple[str, float] | None:
        try:
            low = float(candle["low"])
            high = float(candle["high"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("monitor candle requires numeric high and low") from None
        if not isfinite(low) or not isfinite(high) or low > high:
            raise ValueError("monitor candle has invalid high/low")
        opn = float(candle.get("open", (low + high) / 2))
        if position.side == "long":
            if position.stop_loss_price is not None and opn <= position.stop_loss_price:
                return "stop_loss", opn
            if position.take_profit_price is not None and opn >= position.take_profit_price:
                return "take_profit", opn
            if position.stop_loss_price is not None and low <= position.stop_loss_price:
                return "stop_loss", PaperEngine._gap_stop_price(position, float(position.stop_loss_price), candle)
            if position.take_profit_price is not None and high >= position.take_profit_price:
                return "take_profit", (max(float(position.take_profit_price), float(candle.get("open", position.take_profit_price))) if position.side == "long" else min(float(position.take_profit_price), float(candle.get("open", position.take_profit_price))))
        elif position.side == "short":
            if position.stop_loss_price is not None and opn >= position.stop_loss_price:
                return "stop_loss", opn
            if position.take_profit_price is not None and opn <= position.take_profit_price:
                return "take_profit", opn
            if position.stop_loss_price is not None and high >= position.stop_loss_price:
                return "stop_loss", PaperEngine._gap_stop_price(position, float(position.stop_loss_price), candle)
            if position.take_profit_price is not None and low <= position.take_profit_price:
                return "take_profit", (max(float(position.take_profit_price), float(candle.get("open", position.take_profit_price))) if position.side == "long" else min(float(position.take_profit_price), float(candle.get("open", position.take_profit_price))))
        return None

    @staticmethod
    def _protective_exit(position, candle: dict) -> tuple[str, float] | None:
        """Apply the active stop before TP; same-candle collisions are stop-first."""
        if position.side == "short":
            return PaperEngine._static_protective_exit(position, candle)
        if position.side != "long":
            return None
        low = float(candle.get("low", candle.get("close", 0.0)))
        high = float(candle.get("high", candle.get("close", 0.0)))
        if not isfinite(low) or not isfinite(high) or low <= 0 or low > high:
            raise ValueError("invalid monitor high/low")
        stop_price = float(position.stop_loss_price) if position.stop_loss_price is not None else None
        stop_reason = "stop_loss"
        if position.dynamic_exit is not None:
            state = DynamicExitState.from_dict(position.dynamic_exit)
            policy = state.policy
            if policy.mode == "execute" and state.dynamic_stop_price is not None:
                dynamic_stop = float(state.dynamic_stop_price)
                if stop_price is None or dynamic_stop > stop_price:
                    stop_price = dynamic_stop
                    stop_reason = "dynamic_stop"
            if (
                policy.mode == "execute"
                and policy.adaptive_target
                and state.active_target_price is not None
            ):
                target_price = float(state.active_target_price)
            else:
                target_price = (
                    float(position.take_profit_price)
                    if position.take_profit_price is not None else None
                )
        else:
            target_price = (
                float(position.take_profit_price)
                if position.take_profit_price is not None else None
            )
        opn = float(candle.get("open", (low + high) / 2))
        if stop_price is not None and opn <= stop_price:
            return stop_reason, opn
        if target_price is not None and opn >= target_price:
            return "take_profit", opn
        if stop_price is not None and low <= stop_price:
            return stop_reason, PaperEngine._gap_stop_price(position, stop_price, candle)
        if target_price is not None and high >= target_price:
            return "take_profit", max(target_price, float(candle.get("open", target_price)))
        return None

    @staticmethod
    def _position_ids(account: Account, strategy_id: str) -> list[str]:
        return [
            position_id
            for position_id, position in account.positions.items()
            if position.strategy_id == strategy_id
        ]

    @staticmethod
    def _next_position_id(account: Account, strategy_id: str) -> str:
        if strategy_id not in account.positions:
            return strategy_id
        index = 2
        while f"{strategy_id}{TRANCHE_SEP}{index}" in account.positions:
            index += 1
        return f"{strategy_id}{TRANCHE_SEP}{index}"

    # ---- one cycle -------------------------------------------------------
    def run_cycle(self, *, now: datetime | None = None) -> dict:
        if self._reentry_policy is not None:
            return self._run_auction_cycle(now=now)
        return self._run_legacy_cycle(now=now)

    def _run_legacy_cycle(self, *, now: datetime | None = None) -> dict:
        """Evaluate every strategy once and apply intents to the shared account.
        Returns a summary; never raises because of a single strategy."""
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        account = self._load_account()
        self._flush_pending_fills(account)
        self._migrate_exit_metadata(account)

        # Phase 1: gather each strategy's signal + price + risk basis. A failure
        # here is contained to that strategy so the others still trade.
        plans: list[dict] = []
        prices: dict[str, float] = {}
        errors: dict[str, str] = {}
        for sc in self._strategies:
            monitor_rows = []
            if sc.monitor_timeframe:
                try:
                    monitor_rows = self._provider(sc.symbol, sc.monitor_timeframe, self._candles_limit)
                    if monitor_rows:
                        prices[sc.symbol] = float(monitor_rows[-1]["close"])
                except Exception as exc:
                    errors[f"{sc.id}:monitor"] = f"{type(exc).__name__}: {exc}"
            plans.append({"sc": sc, "signal": None, "duplicate": True, "candle_ts": account.processed_candles.get(sc.id), "monitor_candles": monitor_rows})
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
                if self._execution_mode == "observed_mark":
                    execution_price = mark_price
                prices[sc.symbol] = mark_price
                plans[-1]["candle_ts"] = candle_ts
                signal = None
                if not duplicate:
                    signal = engine.on_candle(candles[-1], StrategyContext(
                        candles=candles, symbol=sc.symbol, timeframe=sc.timeframe,
                        candles_by_timeframe=candles_by_timeframe,
                    ))
                plans[-1] = {"sc": sc, "execution_price": execution_price,
                              "mark_price": mark_price, "atr_risk": _ATR_MULT * _atr(candles),
                              "signal": signal, "candle_ts": candle_ts, "duplicate": duplicate,
                              "monitor_candle": mark_candle if monitor_candles else None,
                              "monitor_candles": monitor_candles}
            except Exception as exc:  # noqa: BLE001 - strict per-strategy isolation
                errors[sc.id] = f"{type(exc).__name__}: {exc}"

        # Phase 2a: protective levels are independent from primary strategy
        # deduplication. An H4 strategy can therefore exit on a fresh closed M15
        # candle even when its H4 signal candle has not changed.
        intents: dict[str, str] = {}
        fills: list[dict] = []
        protectively_closed: set[str] = set()
        for plan in plans:
            sc: StrategyConfig = plan["sc"]
            monitor_candles = plan.get("monitor_candles") or []
            if not monitor_candles:
                continue
            for position_id in self._position_ids(account, sc.id):
                position = account.positions.get(position_id)
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
                    try:
                        protective = self._protective_exit(position, monitor_candle)
                    except Exception as exc:  # noqa: BLE001 - isolate one tranche
                        errors[f"{sc.id}:{position_id}:protective_exit"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                        break
                    position.last_monitor_candle_ts = monitor_candle.get("ts")
                    if protective is None:
                        continue
                    reason, fill_price = protective
                    fill = self._broker.close(
                        account, strategy_id=sc.id, position_id=position_id,
                        price=fill_price, ts=ts, reason=reason,
                    )
                    if fill:
                        intents[sc.id] = f"close_{reason}"
                        protectively_closed.add(sc.id)
                        fills.append(fill)
                    break

        # Equity snapshot AFTER protective closes and BEFORE new entries. Every
        # strategy still sizes from the same post-exit account value.
        equity_for_sizing = account.equity(prices)

        # Phase 2b: turn fresh strategy intents into fills on the shared account.
        for plan in plans:
            sc: StrategyConfig = plan["sc"]
            if sc.id in protectively_closed:
                signal = plan["signal"]
                desired_side = (
                    "long" if signal and signal.side == Side.LONG
                    else "short" if signal and signal.side == Side.SHORT
                    else None
                )
                closed_sides = {
                    fill["side"] for fill in fills
                    if fill["strategy_id"] == sc.id and fill["action"] == "close"
                }
                if desired_side is None or desired_side in closed_sides:
                    if plan["candle_ts"] is not None:
                        account.processed_candles[sc.id] = plan["candle_ts"]
                    continue
            if plan["duplicate"]:
                intents[sc.id] = "duplicate_candle"
                continue
            signal = plan["signal"]
            side = signal.side if signal else None
            if side in (Side.LONG, Side.SHORT):
                if not sc.entry_enabled:
                    fill = None
                    intents[sc.id] = "entry_disabled"
                else:
                    direction = "long" if side == Side.LONG else "short"
                    risk_distance = _entry_risk_distance(
                        plan["execution_price"],
                        signal.suggested_stop,
                        plan["atr_risk"],
                        side=direction,
                    )
                    invalid_short_bracket = (
                        direction == "short"
                        and not _valid_short_bracket(
                            plan["execution_price"],
                            signal.suggested_stop,
                            signal.suggested_take_profit,
                        )
                    )
                    if risk_distance is None or invalid_short_bracket:
                        fill = None
                        intents[sc.id] = (
                            "invalid_bracket" if invalid_short_bracket else "invalid_stop"
                        )
                        continue
                    account.processed_candles[sc.id] = plan["candle_ts"]
                    opposite = [
                        position_id
                        for position_id in self._position_ids(account, sc.id)
                        if account.positions[position_id].side != direction
                    ]
                    reversing = bool(opposite)
                    effective_risk_pct = sc.risk_pct
                    if reversing:
                        for opposite_id in opposite:
                            reverse_fill = self._broker.close(
                                account,
                                strategy_id=sc.id,
                                position_id=opposite_id,
                                price=plan["execution_price"],
                                ts=ts,
                                reason=f"reverse_to_{direction}: {signal.entry_reason}",
                            )
                            if reverse_fill:
                                fills.append(reverse_fill)
                        if any(
                            account.positions[position_id].side != direction
                            for position_id in self._position_ids(account, sc.id)
                        ):
                            intents[sc.id] = "reverse_close_failed"
                            continue
                    entry_equity = (
                        account.equity(prices) if reversing else equity_for_sizing
                    )
                    open_total_risk_usd = sum(
                        position.budget_risk_usd
                        for position in account.positions.values()
                    )
                    open_symbol_risk_usd = sum(
                        position.budget_risk_usd
                        for position in account.positions.values()
                        if position.symbol == sc.symbol
                    )
                    candidate_risk_usd = effective_risk_pct * entry_equity
                    if (
                        open_total_risk_usd + candidate_risk_usd
                        > self._max_total_stop_risk_pct * entry_equity
                    ):
                        fill = None
                        intents[sc.id] = "risk_cap_total"
                        continue
                    if (
                        open_symbol_risk_usd + candidate_risk_usd
                        > self._max_symbol_stop_risk_pct * entry_equity
                    ):
                        fill = None
                        intents[sc.id] = "risk_cap_symbol"
                        continue
                    fill = self._broker.open(
                        account, strategy_id=sc.id, symbol=sc.symbol, side=direction,
                        price=plan["execution_price"],
                        atr_risk=plan["atr_risk"], risk_distance=risk_distance,
                        risk_pct=effective_risk_pct,
                        equity_for_sizing=entry_equity, ts=ts, entry_reason=signal.entry_reason,
                        exit_policy=sc.exit_policy, monitor_timeframe=sc.monitor_timeframe,
                        stop_loss_price=signal.suggested_stop,
                        take_profit_price=signal.suggested_take_profit,
                        sl_basis="strategy_suggested" if signal.suggested_stop is not None else "",
                        reward_risk_ratio=sc.reward_risk_ratio,
                        max_leverage=self._max_leverage)
                    if fill and sc.id in account.positions and plan.get("monitor_candle") is not None:
                        account.positions[sc.id].last_monitor_candle_ts = plan["monitor_candle"].get("ts")
                    intents[sc.id] = (
                        "reverse_open" if fill and reversing
                        else "open" if fill else "hold"
                    )
            elif side == Side.EXIT:
                account.processed_candles[sc.id] = plan["candle_ts"]
                closed: list[dict] = []
                for position_id in self._position_ids(account, sc.id):
                    fill = self._broker.close(
                        account, strategy_id=sc.id, position_id=position_id,
                        price=plan["execution_price"], ts=ts,
                        reason=signal.entry_reason,
                    )
                    if fill:
                        closed.append(fill)
                fills.extend(closed)
                intents[sc.id] = "close" if closed else "no_trade"
                fill = None
            else:
                account.processed_candles[sc.id] = plan["candle_ts"]
                intents[sc.id] = "no_trade"
                fill = None
            if fill:
                fills.append(fill)

        equity_after = account.equity(prices)
        if fills:
            self._commit_fills(account, fills)
        else:
            self._save_account(account)

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

    def _monitor_plans(self, account, plans, ts, errors, *, only_positions=None, historical=False):
        intents: dict[str, str] = {}
        fills: list[dict] = []
        protectively_closed: set[str] = set()
        for plan in plans:
            sc: StrategyConfig = plan["sc"]
            monitor_candles = plan.get("monitor_candles") or []
            if not monitor_candles:
                for position_id in self._position_ids(account, sc.id):
                    position = account.positions.get(position_id)
                    if position is not None and position.dynamic_exit is not None:
                        errors[f"{sc.id}:{position_id}:dynamic_exit"] = (
                            "monitor_data_missing: no closed monitor candles"
                        )
                continue
            for position_id in self._position_ids(account, sc.id):
                if only_positions is not None and position_id not in only_positions:
                    continue
                position = account.positions.get(position_id)
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
                dynamic_history_gap = False
                if position.dynamic_exit is not None and position.last_monitor_candle_ts is not None:
                    dynamic_history_gap = self._monitor_history_has_gap(
                        monitor_candles,
                        cursor=position.last_monitor_candle_ts,
                        timeframe=sc.monitor_timeframe,
                    )
                    if dynamic_history_gap:
                        errors[f"{sc.id}:{position_id}:dynamic_exit"] = (
                            "monitor_history_gap: cursor predates fetched candles"
                        )
                for monitor_candle in pending_monitor_candles:
                    prior_cursor = position.last_monitor_candle_ts
                    prior_dynamic_state = position.dynamic_exit
                    if not dynamic_history_gap:
                        position.last_monitor_candle_ts = monitor_candle.get("ts")
                    if position.dynamic_exit is not None and not dynamic_history_gap:
                        try:
                            shadow_state = DynamicExitState.from_dict(
                                position.dynamic_exit
                            )
                            shadow_policy = shadow_state.policy
                            shadow_stop = shadow_state.dynamic_stop_price
                            shadow_low = float(
                                monitor_candle.get(
                                    "low", monitor_candle.get("close", 0.0)
                                )
                            )
                            if (
                                shadow_policy.mode == "observe"
                                and not shadow_state.hypothetical_closed
                                and shadow_stop is not None
                                and shadow_low <= shadow_stop
                            ):
                                latest_candle = monitor_candles[-1]
                                recovered = not historical and monitor_candle is not latest_candle
                                shadow_price = (
                                    float(latest_candle["close"])
                                    if recovered else min(
                                        float(shadow_stop),
                                        float(monitor_candle.get("open", shadow_stop)),
                                    )
                                )
                                decision_id = self._dynamic_id(
                                    position_id,
                                    monitor_candle.get("ts"),
                                    "dynamic_stop",
                                    shadow_policy.version,
                                )
                                next_shadow_state = replace(
                                    shadow_state,
                                    hypothetical_closed=True,
                                    hypothetical_exit_ts=monitor_candle.get("ts"),
                                    hypothetical_exit_reason="dynamic_stop",
                                    hypothetical_exit_price=shadow_price,
                                    last_candle_ts=monitor_candle.get("ts"),
                                )
                                self._record_dynamic_decision({
                                    "decision_id": decision_id,
                                    "observed_at": ts,
                                    "strategy_id": sc.id,
                                    "position_id": position_id,
                                    "symbol": position.symbol,
                                    "mode": shadow_policy.mode,
                                    "policy_version": shadow_policy.version,
                                    "decision_candle_ts": monitor_candle.get("ts"),
                                    "action": ExitAction.CLOSE.value,
                                    "reason": "dynamic_stop",
                                    "desired_price": shadow_price,
                                    "recovered_after_gap": recovered,
                                    "mfe_r": shadow_state.mfe_r,
                                    "dynamic_stop_price": shadow_stop,
                                })
                                position.dynamic_exit = next_shadow_state.to_dict()
                        except Exception as exc:  # noqa: BLE001 - shadow audit retries
                            dynamic_history_gap = True
                            position.last_monitor_candle_ts = prior_cursor
                            position.dynamic_exit = prior_dynamic_state
                            errors[f"{sc.id}:{position_id}:dynamic_exit_audit"] = (
                                f"{type(exc).__name__}: {exc}"
                            )
                            if position.dynamic_exit is not None:
                                try:
                                    retry_state = DynamicExitState.from_dict(
                                        position.dynamic_exit
                                    )
                                    position.last_monitor_candle_ts = (
                                        retry_state.last_candle_ts
                                    )
                                except Exception:  # noqa: BLE001 - main guard below
                                    pass
                    try:
                        protective = self._protective_exit(position, monitor_candle)
                    except Exception as exc:  # noqa: BLE001 - isolate one tranche
                        errors[f"{sc.id}:{position_id}:dynamic_exit"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                        try:
                            protective = self._static_protective_exit(
                                position, monitor_candle
                            )
                        except Exception as static_exc:  # noqa: BLE001
                            dynamic_history_gap = True
                            position.last_monitor_candle_ts = prior_cursor
                            position.dynamic_exit = prior_dynamic_state
                            errors[f"{sc.id}:{position_id}:protective_exit"] = (
                                f"{type(static_exc).__name__}: {static_exc}"
                            )
                            continue
                    if protective is None:
                        if position.dynamic_exit is None:
                            continue
                        if dynamic_history_gap:
                            continue
                        try:
                            state = DynamicExitState.from_dict(position.dynamic_exit)
                            policy = state.policy
                            if policy.mode == "observe" and state.hypothetical_closed:
                                continue
                            candle_index = monitor_candles.index(monitor_candle)
                            evaluation = evaluate_dynamic_exit(
                                position_id=position_id,
                                entry_price=position.entry_px,
                                atr_risk=position.atr_risk,
                                initial_stop_price=position.stop_loss_price,
                                initial_target_price=position.take_profit_price,
                                previous_state=state,
                                candle=monitor_candle,
                                candles_asof=monitor_candles[:candle_index + 1],
                            )
                            decision = evaluation.decision
                            decision_id = self._dynamic_id(
                                position_id,
                                decision.decision_candle_ts,
                                decision.reason_code,
                                policy.version,
                            )
                            decision_record = {
                                "decision_id": decision_id,
                                "observed_at": ts,
                                "strategy_id": sc.id,
                                "position_id": position_id,
                                "symbol": position.symbol,
                                "mode": policy.mode,
                                "policy_version": policy.version,
                                "decision_candle_ts": decision.decision_candle_ts,
                                "action": decision.action.value,
                                "reason": decision.reason_code,
                                "desired_price": decision.desired_price,
                                "mfe_r": evaluation.next_state.mfe_r,
                                "dynamic_stop_price": (
                                    evaluation.next_state.dynamic_stop_price
                                ),
                                "active_target_price": (
                                    evaluation.next_state.active_target_price
                                ),
                                "active_target_r": (
                                    evaluation.next_state.active_target_r
                                ),
                                "target_extensions": (
                                    evaluation.next_state.target_extensions
                                ),
                                "target_strength_score": (
                                    evaluation.next_state.target_strength_score
                                ),
                            }
                            try:
                                self._record_dynamic_decision(decision_record)
                            except Exception as exc:  # noqa: BLE001 - audit is secondary
                                errors[f"{sc.id}:{position_id}:dynamic_exit_audit"] = (
                                    f"{type(exc).__name__}: {exc}"
                                )
                                if policy.mode == "observe":
                                    position.last_monitor_candle_ts = prior_cursor
                                    position.dynamic_exit = prior_dynamic_state
                                    dynamic_history_gap = True
                                    continue
                            position.dynamic_exit = evaluation.next_state.to_dict()
                            if (
                                decision.action != ExitAction.CLOSE
                                or policy.mode != "execute"
                            ):
                                continue
                            latest_candle = monitor_candles[-1]
                            recovered = not historical and monitor_candle is not latest_candle
                            fill_price = float(
                                latest_candle["close"]
                                if recovered else decision.desired_price
                            )
                            fill = self._broker.close(
                                account,
                                strategy_id=sc.id,
                                position_id=position_id,
                                price=fill_price,
                                ts=ts,
                                reason=decision.reason_code,
                            )
                            if fill:
                                account.processed_candles[sc.id] = plan["candle_ts"]
                                fill["fill_id"] = self._dynamic_id(
                                    "fill", decision_id
                                )
                                fill["decision_id"] = decision_id
                                fill["decision_candle_ts"] = (
                                    decision.decision_candle_ts
                                )
                                fill["recovered_after_gap"] = recovered
                                intents[sc.id] = f"close_{decision.reason_code}"
                                protectively_closed.add(sc.id)
                                fills.append(fill)
                            break
                        except Exception as exc:  # noqa: BLE001 - isolate one tranche
                            dynamic_history_gap = True
                            position.last_monitor_candle_ts = prior_cursor
                            position.dynamic_exit = prior_dynamic_state
                            errors[f"{sc.id}:{position_id}:dynamic_exit"] = (
                                f"{type(exc).__name__}: {exc}"
                            )
                            continue
                    else:
                        reason, fill_price = protective
                        recovered = False
                        if reason in {"dynamic_stop", "stop_loss"}:
                            latest_candle = monitor_candles[-1]
                            recovered = not historical and monitor_candle is not latest_candle
                            if recovered:
                                fill_price = float(latest_candle["close"])
                            else:
                                fill_price = self._gap_stop_price(position, fill_price, monitor_candle)
                        fill = self._broker.close(
                            account,
                            strategy_id=sc.id,
                            position_id=position_id,
                            price=fill_price,
                            ts=ts,
                            reason=reason,
                        )
                        if fill:
                            fill["decision_candle_ts"] = monitor_candle.get("ts")
                            fill["recovered_after_gap"] = recovered
                            intents[sc.id] = f"close_{reason}"
                            protectively_closed.add(sc.id)
                            fills.append(fill)
                            if reason == "dynamic_stop":
                                account.processed_candles[sc.id] = plan["candle_ts"]
                                state = DynamicExitState.from_dict(
                                    fill["dynamic_exit"]
                                )
                                fill["decision_candle_ts"] = monitor_candle.get("ts")
                                fill["recovered_after_gap"] = recovered
                                fill["fill_id"] = self._dynamic_id(
                                    "fill",
                                    position_id,
                                    monitor_candle.get("ts"),
                                    reason,
                                    state.policy.version,
                                )
                            elif fill.get("dynamic_exit") is not None:
                                account.processed_candles[sc.id] = plan["candle_ts"]
                                fill["fill_id"] = self._dynamic_id(
                                    "fill",
                                    fill["action"],
                                    fill["position_id"],
                                    monitor_candle.get("ts"),
                                    reason,
                                )
                        break

        return intents, fills, protectively_closed

    def _prepare_auction_plans(self, account: Account, ts: str, fetch: CandleProvider) -> tuple[list[dict], dict[str, float], dict[str, str]]:
        """Prepare signals in order, retaining cursor updates and pending exits."""
        plans: list[dict] = []
        prices: dict[str, float] = {}
        errors: dict[str, str] = {}
        for sc in self._strategies:
            # Protection data is independent of secondary indicators and observers.
            monitor_rows = []
            if sc.monitor_timeframe:
                try:
                    monitor_rows = fetch(sc.symbol, sc.monitor_timeframe, self._candles_limit)
                    if monitor_rows:
                        prices[sc.symbol] = float(monitor_rows[-1]["close"])
                except Exception as exc:
                    errors[f"{sc.id}:monitor"] = f"{type(exc).__name__}: {exc}"
            fallback = {
                "sc": sc, "signal": None, "duplicate": True,
                "candle_ts": account.processed_candles.get(sc.id),
                "monitor_candles": monitor_rows,
                "monitor_candle": monitor_rows[-1] if monitor_rows else None,
            }
            forced_exit = None
            try:
                if self._execution_mode == "observed_mark" and sc.engine == "opening_range":
                    from orum.market_calendar import NEW_YORK, session_bounds
                    from datetime import timedelta
                    from orum.strategies.base import Signal
                    local = datetime.fromisoformat(ts).astimezone(NEW_YORK)
                    session = session_bounds(local.date())
                    positions = [account.positions[key] for key in self._position_ids(account, sc.id)]
                    for position in positions:
                        opened = position.opened_ts
                        try:
                            opened_dt = (datetime.fromisoformat(opened) if isinstance(opened, str) else datetime.fromtimestamp(float(opened)/1000, timezone.utc)).astimezone(NEW_YORK)
                            overdue = opened_dt.date() < local.date()
                        except (ValueError, TypeError):
                            overdue = False
                        if overdue or session is None or local >= session[1] - timedelta(minutes=15):
                            position.pending_session_exit = True
                    pending_exit = any(position.pending_session_exit for position in positions)
                    if pending_exit:
                        mark_time = datetime.fromtimestamp(float(monitor_rows[-1]["ts"]) / 1000, timezone.utc).astimezone(NEW_YORK) if monitor_rows else None
                        usable = session is not None and session[0] <= local < session[1] and mark_time is not None and session[0] <= mark_time < local and (local - mark_time).total_seconds() <= 600
                        if usable:
                            forced_exit = Signal(Side.EXIT, sc.symbol, sc.timeframe, "opening_range_session_close")
                            fallback.update(signal=forced_exit, duplicate=False, execution_price=float(monitor_rows[-1]["close"]), candle_ts=monitor_rows[-1]["ts"])
                        else:
                            errors[f"{sc.id}:session"] = "missed_session_exit: await current-session mark"
            except Exception as exc:
                errors[f"{sc.id}:session"] = f"{type(exc).__name__}: {exc}"
            plans.append(fallback)
            try:
                engine = self._engines[sc.id]
                if self._execution_mode == "observed_mark" and sc.monitor_timeframe and not monitor_rows:
                    raise ValueError("monitor data required for observed execution")
                shadow_filter = self._shadow_regime_filters.get(sc.symbol)
                required_timeframes = list(
                    dict.fromkeys([
                        sc.timeframe,
                        *getattr(engine, "required_timeframes", []),
                        *([sc.monitor_timeframe] if sc.monitor_timeframe else []),
                    ])
                )
                candles_by_timeframe = {
                    timeframe: (monitor_rows if timeframe == sc.monitor_timeframe else fetch(
                        sc.symbol, timeframe, self._candles_limit
                    ))
                    for timeframe in required_timeframes
                }
                if shadow_filter is not None and shadow_filter["timeframe"] not in candles_by_timeframe:
                    try:
                        candles_by_timeframe[shadow_filter["timeframe"]] = fetch(
                            sc.symbol, shadow_filter["timeframe"], self._candles_limit
                        )
                    except Exception as exc:
                        errors[f"{sc.id}:shadow_regime"] = f"{type(exc).__name__}: {exc}"
                candles = candles_by_timeframe.get(sc.timeframe, [])
                if not candles:
                    errors[sc.id] = "no candles"
                    continue
                signal_candles_by_timeframe = candles_by_timeframe
                signal_candles = candles
                execution_candle = candles[-1]
                if self._execution_mode == "next_open":
                    try:
                        execution_ts = float(execution_candle["ts"])
                    except (KeyError, TypeError, ValueError):
                        errors[sc.id] = "invalid execution candle timestamp"
                        continue
                    signal_candles_by_timeframe = {}
                    for timeframe, rows in candles_by_timeframe.items():
                        interval = _TIMEFRAME_MS.get(timeframe)
                        if interval is None:
                            raise ValueError(
                                f"unsupported timeframe {timeframe!r}"
                            )
                        signal_candles_by_timeframe[timeframe] = [
                            candle for candle in rows
                            if float(candle["ts"]) + interval <= execution_ts
                        ]
                    signal_candles = signal_candles_by_timeframe.get(
                        sc.timeframe, []
                    )
                    if not signal_candles:
                        errors[sc.id] = "no prior closed candle for next_open"
                        continue
                signal_candle = signal_candles[-1]
                candle_ts = signal_candle.get("ts")
                duplicate = account.processed_candles.get(sc.id) == candle_ts
                monitor_candles = (
                    candles_by_timeframe.get(sc.monitor_timeframe, [])
                    if sc.monitor_timeframe else []
                )
                mark_candle = monitor_candles[-1] if monitor_candles else candles[-1]
                execution_price = float(
                    execution_candle[
                        "open" if self._execution_mode == "next_open" else "close"
                    ]
                )
                mark_price = float(mark_candle["close"])
                if self._execution_mode == "next_open":
                    mark_price = execution_price
                if self._execution_mode == "observed_mark":
                    execution_price = mark_price
                prices[sc.symbol] = mark_price
                fallback["candle_ts"] = candle_ts
                signal = None
                if not duplicate:
                    signal = engine.on_candle(
                        signal_candle,
                        StrategyContext(
                            candles=signal_candles,
                            symbol=sc.symbol,
                            timeframe=sc.timeframe,
                            candles_by_timeframe=signal_candles_by_timeframe,
                        ),
                    )
                if forced_exit is not None:
                    signal, duplicate = forced_exit, False
                if getattr(engine, "data_error", None):
                    errors[f"{sc.id}:data"] = str(engine.data_error)
                plans[-1] = {
                    "sc": sc,
                    "execution_candle_ts": execution_candle["ts"],
                    "price_asof_ts": (mark_candle["ts"] + _TIMEFRAME_MS[sc.monitor_timeframe or sc.timeframe]),
                    "candles_by_timeframe": candles_by_timeframe,
                    "execution_price": execution_price,
                    "mark_price": mark_price,
                    "atr_risk": _ATR_MULT * _atr(signal_candles),
                    "signal": signal,
                    "skipped_signal_ts": getattr(engine, "skipped_signal_ts", None),
                    "candle_ts": candle_ts,
                    "duplicate": duplicate,
                    "signal_candles_by_timeframe": signal_candles_by_timeframe,
                    "monitor_candle": mark_candle if monitor_candles else None,
                    "monitor_candles": monitor_candles,
                }
            except Exception as exc:  # noqa: BLE001 - per-strategy isolation
                errors[sc.id] = f"{type(exc).__name__}: {exc}"
        return plans, prices, errors

    def _run_auction_cycle(self, *, now: datetime | None = None) -> dict:
        """Run an explicit merit auction with optional per-strategy topups."""
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        account = self._load_account()
        self._flush_pending_fills(account)
        self._migrate_exit_metadata(account)

        observed_at_ms = int(datetime.fromisoformat(ts).timestamp() * 1000)
        def fetch(symbol, timeframe, limit):
            rows = self._provider(symbol, timeframe, limit)
            if self._execution_mode != "observed_mark":
                return rows
            if not rows:
                raise ValueError("no closed candles")
            interval = _TIMEFRAME_MS[timeframe]
            stamps = [float(row["ts"]) for row in rows]
            if any(right <= left for left, right in zip(stamps, stamps[1:])):
                raise ValueError("duplicate or reversed candles")
            for row in rows:
                opn, high, low, close, vol = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
                if not all(isfinite(v) for v in (opn, high, low, close, vol)) or min(opn, high, low, close) <= 0 or vol < 0 or high < max(opn, close) or low > min(opn, close):
                    raise ValueError("invalid OHLCV")
            if symbol != "NVDA":
                if any(right - left != interval for left, right in zip(stamps, stamps[1:])):
                    raise ValueError("candle history gap")
                age = observed_at_ms - (stamps[-1] + interval)
                if age < 0 or age >= interval + 60_000:
                    raise ValueError("stale or unclosed candles")
            key = f"{symbol}:{timeframe}"
            if stamps[-1] < account.market_cursors.get(key, stamps[-1]):
                raise ValueError("market timestamp moved backwards")
            account.market_cursors[key] = int(stamps[-1])
            return rows

        plans, prices, errors = self._prepare_auction_plans(account, ts, fetch)

        if self._valuation_marks is not None:
            prices = dict(self._valuation_marks)
        entry_boundaries = {plan["execution_candle_ts"] for plan in plans if "execution_candle_ts" in plan}
        ambiguous_replay = self._execution_mode == "next_open" and (
            len(entry_boundaries) > 1 or bool(errors)
            or (entry_boundaries and account.last_event_ts is not None and min(entry_boundaries) < account.last_event_ts)
        )
        if ambiguous_replay:
            errors["execution_timeline"] = "next_open requires complete aligned data and no account events after the entry boundary"
        pre_entry_plans = plans
        if self._execution_mode == "next_open" and not ambiguous_replay:
            pre_entry_plans = [{**plan, "monitor_candles": [row for row in plan.get("monitor_candles", []) if row["ts"] < plan.get("execution_candle_ts", float("inf"))]} for plan in plans]
        intents, fills, protectively_closed = self._monitor_plans(account, pre_entry_plans, ts, errors, historical=self._execution_mode == "next_open")
        if self._execution_mode == "next_open" and not ambiguous_replay:
            # An existing bracket crossed at T is executable before new risk
            # is allocated at T. Do not advance the intrabar monitor cursor.
            for plan in plans:
                boundary = plan.get("execution_candle_ts")
                opening = next((row for row in plan.get("monitor_candles", []) if row["ts"] == boundary), None)
                if opening is None:
                    continue
                for position_id in self._position_ids(account, plan["sc"].id):
                    position = account.positions[position_id]
                    opn = float(opening["open"])
                    try:
                        protective = self._protective_exit(position, {"open": opn, "high": opn, "low": opn, "close": opn})
                    except Exception as exc:
                        errors[f"{position_id}:gap"] = f"{type(exc).__name__}: {exc}"
                        ambiguous_replay = True
                        continue
                    if protective is not None:
                        reason, price = protective
                        fill = self._broker.close(account, strategy_id=position.strategy_id, position_id=position_id, price=price, ts=ts, reason=reason)
                        if fill:
                            fill.update(decision_candle_ts=boundary, gap_at_open=True, recovered_after_gap=False)
                            fills.append(fill)
        if fills:
            self._commit_fills(account, fills)
        else:
            self._save_account(account)

        equity_for_sizing = account.equity(prices)
        requests: list[dict] = []
        auction: list[dict] = []
        for plan in plans:
            sc: StrategyConfig = plan["sc"]
            if sc.id in protectively_closed:
                signal = plan["signal"]
                desired_side = (
                    "long" if signal and signal.side == Side.LONG
                    else "short" if signal and signal.side == Side.SHORT
                    else None
                )
                closed_sides = {
                    fill["side"] for fill in fills
                    if fill["strategy_id"] == sc.id and fill["action"] == "close"
                }
                if desired_side is None or desired_side in closed_sides:
                    if plan["candle_ts"] is not None:
                        account.processed_candles[sc.id] = plan["candle_ts"]
                    continue
            if plan["duplicate"]:
                intents[sc.id] = "duplicate_candle"
                continue
            signal = plan["signal"]
            side = signal.side if signal else None
            if side in (Side.LONG, Side.SHORT):
                if ambiguous_replay:
                    intents[sc.id] = "ambiguous_replay_timeline"
                    continue
                if self._execution_mode == "observed_mark":
                    available = float(plan["candle_ts"]) + _TIMEFRAME_MS[sc.timeframe]
                    age = observed_at_ms - available
                    late = age < 0 or age >= _TIMEFRAME_MS[sc.timeframe]
                    if sc.symbol == "NVDA":
                        from orum.market_calendar import NEW_YORK, session_bounds
                        local = datetime.fromisoformat(ts).astimezone(NEW_YORK)
                        session = session_bounds(local.date())
                        late = late or session is None or not session[0] <= local < session[1]
                    if late:
                        account.processed_candles[sc.id] = plan["candle_ts"]
                        intents[sc.id] = "expired_signal"
                        continue
                direction = "long" if side == Side.LONG else "short"
                if direction not in self._allowed_entry_sides:
                    account.processed_candles[sc.id] = plan["candle_ts"]
                    intents[sc.id] = f"{direction}_disabled"
                    continue
                risk_distance = _entry_risk_distance(
                    plan["execution_price"],
                    signal.suggested_stop,
                    plan["atr_risk"],
                    side=direction,
                )
                invalid_short_bracket = (
                    direction == "short"
                    and not _valid_short_bracket(
                        plan["execution_price"],
                        signal.suggested_stop,
                        signal.suggested_take_profit,
                    )
                )
                if risk_distance is None or invalid_short_bracket:
                    intents[sc.id] = (
                        "invalid_bracket" if invalid_short_bracket else "invalid_stop"
                    )
                    continue
                shadow_regime = None
                try:
                    shadow_regime = self._shadow_regime(plan, direction)
                    if shadow_regime is not None:
                        self._append(self._shadow_regime_path, {
                            "ts": ts,
                            "strategy_id": sc.id,
                            "symbol": sc.symbol,
                            "direction": direction,
                            "signal_candle_ts": plan["candle_ts"],
                            **shadow_regime,
                        })
                except Exception as exc:
                    errors[f"{sc.id}:shadow_regime"] = f"{type(exc).__name__}: {exc}"
                account.processed_candles[sc.id] = plan["candle_ts"]
                requests.append({
                    "plan": plan,
                    "direction": direction,
                    "risk_distance": risk_distance,
                    "base_risk_pct": sc.risk_pct,
                    "entry_enabled": sc.entry_enabled,
                    "shadow_regime": shadow_regime,
                })
            elif side == Side.EXIT:
                if not self._position_ids(account, sc.id):
                    account.processed_candles[sc.id] = plan["candle_ts"]
                    intents[sc.id] = "no_trade"
                    continue
                if ambiguous_replay:
                    intents[sc.id] = "ambiguous_replay_timeline"
                    continue
                if self._execution_mode == "observed_mark" and sc.symbol == "NVDA":
                    from orum.market_calendar import NEW_YORK, session_bounds
                    local = datetime.fromisoformat(ts).astimezone(NEW_YORK)
                    session = session_bounds(local.date())
                    mark_time = datetime.fromtimestamp(float(plan["monitor_candle"]["ts"]) / 1000, timezone.utc).astimezone(NEW_YORK)
                    if session is None or not session[0] <= local < session[1] or mark_time < session[0]:
                        errors[f"{sc.id}:session"] = "missed_session_exit: await next available session price"
                        intents[sc.id] = "missed_session_exit"
                        continue
                account.processed_candles[sc.id] = plan["candle_ts"]
                closed_any = False
                for position_id in self._position_ids(account, sc.id):
                    fill = self._broker.close(
                        account,
                        strategy_id=sc.id,
                        position_id=position_id,
                        price=plan["execution_price"],
                        ts=ts,
                        reason=signal.entry_reason,
                    )
                    if fill:
                        closed_any = True
                        fills.append(fill)
                        if fill.get("dynamic_exit") is not None:
                            fill["fill_id"] = self._dynamic_id(
                                "fill", fill["action"], fill["position_id"],
                                plan["candle_ts"], signal.entry_reason,
                            )
                intents[sc.id] = "close" if closed_any else "no_trade"
            else:
                skipped_ts = plan.get("skipped_signal_ts")
                previous_ts = account.processed_candles.get(sc.id)
                if skipped_ts is not None and (previous_ts is None or skipped_ts > previous_ts):
                    auction.append({"ts": ts, "strategy_id": sc.id, "symbol": sc.symbol,
                                    "signal_candle_ts": skipped_ts, "outcome": "missed_m5_signal",
                                    "execution_method": self._execution_mode + "_v2"})
                    intents[sc.id] = "missed_m5_signal"
                account.processed_candles[sc.id] = plan["candle_ts"]
                intents.setdefault(sc.id, "no_trade")

        # Persisted positions may outlive a removed strategy; they still need marks.
        for position in account.positions.values():
            if position.symbol in prices:
                continue
            try:
                rows = fetch(position.symbol, position.monitor_timeframe or "15m", self._candles_limit)
                if self._execution_mode == "next_open" and len(entry_boundaries) == 1:
                    boundary = next(iter(entry_boundaries))
                    aligned = [row for row in rows if row["ts"] == boundary]
                    closed = [row for row in rows if row["ts"] + _TIMEFRAME_MS[position.monitor_timeframe or "15m"] <= boundary]
                    mark = float(aligned[-1]["open"] if aligned else closed[-1]["close"])
                else:
                    mark = float(rows[-1]["close"])
                if not isfinite(mark) or mark <= 0:
                    raise ValueError("invalid mark")
                prices[position.symbol] = mark
            except Exception as exc:
                errors[f"{position.symbol}:valuation"] = f"{type(exc).__name__}: {exc}"
        equity_for_sizing = account.equity(prices)
        entry_drawdown = self._entry_drawdown(equity_for_sizing)
        entry_drawdown_multiplier = self._entry_drawdown_multiplier(entry_drawdown)
        drawdown_halt = entry_drawdown_multiplier <= 0
        unpriced_symbols = {position.symbol for position in account.positions.values()} - prices.keys()
        if unpriced_symbols:
            errors["valuation"] = "missing marks: " + ", ".join(sorted(unpriced_symbols))
        requests.sort(key=lambda request: self._merit_key[request["plan"]["sc"].id])
        for request in requests:
            plan = request["plan"]
            sc: StrategyConfig = plan["sc"]
            held = self._position_ids(account, sc.id)
            direction = request["direction"]
            opposite = [
                position_id for position_id in held
                if account.positions[position_id].side != direction
            ]
            same_direction = [
                position_id for position_id in held
                if account.positions[position_id].side == direction
            ]
            reversing = bool(opposite)
            if reversing:
                for opposite_id in opposite:
                    reverse_fill = self._broker.close(
                        account,
                        strategy_id=sc.id,
                        position_id=opposite_id,
                        price=plan["execution_price"],
                        ts=ts,
                        reason=f"reverse_to_{direction}: {plan['signal'].entry_reason}",
                    )
                    if not reverse_fill:
                        continue
                    fills.append(reverse_fill)
                    if reverse_fill.get("dynamic_exit") is not None:
                        reverse_fill["fill_id"] = self._dynamic_id(
                            "fill", reverse_fill["action"],
                            reverse_fill["position_id"], plan["candle_ts"],
                            f"reverse_to_{direction}",
                        )
                if self._position_ids(account, sc.id):
                    intents[sc.id] = "reverse_close_failed"
                    continue
                equity_for_sizing = account.equity(prices)
            if unpriced_symbols:
                intents[sc.id] = "valuation_unavailable"
                continue
            if not request["entry_enabled"]:
                intents[sc.id] = "reverse_close" if reversing else "entry_disabled"
                continue
            entry_drawdown = self._entry_drawdown(equity_for_sizing)
            entry_drawdown_multiplier = self._entry_drawdown_multiplier(entry_drawdown)
            drawdown_halt = entry_drawdown_multiplier <= 0
            request["effective_risk_pct"] = (
                request["base_risk_pct"] * entry_drawdown_multiplier
            )
            if self._dynamic_risk_shadow is not None:
                try:
                    proposal = self._dynamic_risk_shadow.propose(
                        strategy_id=sc.id,
                        base_risk_pct=request["base_risk_pct"],
                        drawdown_multiplier=entry_drawdown_multiplier,
                        risk_distance=request["risk_distance"],
                        atr_risk=plan["atr_risk"],
                    )
                    request["dynamic_risk_shadow"] = proposal
                    self._append(self._dynamic_risk_shadow_path, {
                        "ts": ts,
                        "strategy_id": sc.id,
                        "symbol": sc.symbol,
                        "direction": direction,
                        "signal_candle_ts": plan["candle_ts"],
                        "execution_price": plan["execution_price"],
                        "risk_distance": request["risk_distance"],
                        "atr_risk": plan["atr_risk"],
                        "entry_drawdown": entry_drawdown,
                        **proposal,
                    })
                except Exception as exc:
                    errors[f"{sc.id}:dynamic_risk_shadow"] = f"{type(exc).__name__}: {exc}"
            if drawdown_halt:
                intents[sc.id] = "drawdown_kill_switch"
                auction.append({
                    "ts": ts,
                    "strategy_id": sc.id,
                    "symbol": sc.symbol,
                    "direction": direction,
                    "requested_risk_usd": round(
                        request["base_risk_pct"] * equity_for_sizing, 6
                    ),
                    "effective_risk_usd": 0.0,
                    "granted_risk_usd": 0.0,
                    "equity_for_sizing": round(equity_for_sizing, 6),
                    "entry_drawdown": round(entry_drawdown, 8),
                    "entry_drawdown_multiplier": entry_drawdown_multiplier,
                    "outcome": "drawdown_kill_switch",
                })
                continue
            symbol_position_limit = self._max_open_positions_by_symbol.get(
                sc.symbol
            )
            open_symbol_positions = sum(
                position.symbol == sc.symbol
                for position in account.positions.values()
            )
            if (
                symbol_position_limit is not None
                and open_symbol_positions >= symbol_position_limit
            ):
                intents[sc.id] = "position_cap_symbol"
                auction.append({
                    "ts": ts,
                    "strategy_id": sc.id,
                    "symbol": sc.symbol,
                    "direction": direction,
                    "requested_risk_usd": round(
                        request["effective_risk_pct"] * equity_for_sizing, 6
                    ),
                    "granted_risk_usd": 0.0,
                    "equity_for_sizing": round(equity_for_sizing, 6),
                    "open_symbol_positions": open_symbol_positions,
                    "outcome": "position_cap_symbol",
                })
                continue
            requested_usd = request["effective_risk_pct"] * equity_for_sizing
            open_total_risk_usd = sum(
                position.budget_risk_usd
                for position in account.positions.values()
            )
            open_symbol_risk_usd = sum(
                position.budget_risk_usd
                for position in account.positions.values()
                if position.symbol == sc.symbol
            )
            remaining_total = (
                self._max_total_stop_risk_pct * equity_for_sizing
                - open_total_risk_usd
            )
            remaining_thesis = (
                self._max_symbol_stop_risk_pct * equity_for_sizing
                - open_symbol_risk_usd
            )
            symbol_notional_limit_pct = self._max_symbol_notional_pct.get(
                sc.symbol
            )
            open_symbol_notional = sum(
                position.notional_usd
                for position in account.positions.values()
                if position.symbol == sc.symbol
            )
            remaining_symbol_notional = (
                symbol_notional_limit_pct * equity_for_sizing
                - open_symbol_notional
                if symbol_notional_limit_pct is not None else None
            )
            record = {
                "ts": ts,
                "strategy_id": sc.id,
                "symbol": sc.symbol,
                "direction": direction,
                "requested_risk_usd": round(requested_usd, 6),
                "base_risk_pct": request["base_risk_pct"],
                "effective_risk_pct": request["effective_risk_pct"],
                "entry_drawdown": round(entry_drawdown, 8),
                "entry_drawdown_multiplier": entry_drawdown_multiplier,
                "remaining_thesis_usd": round(remaining_thesis, 6),
                "remaining_total_usd": round(remaining_total, 6),
                "equity_for_sizing": round(equity_for_sizing, 6),
                "held_tranches": len(same_direction),
                "shadow_regime": request["shadow_regime"],
                "dynamic_risk_shadow": request.get("dynamic_risk_shadow"),
            }
            if same_direction and self._reentry_policy == "hold":
                intents[sc.id] = "reentry_hold"
                auction.append({
                    **record,
                    "granted_risk_usd": 0.0,
                    "outcome": "reentry_hold",
                })
                continue
            granted = min(requested_usd, remaining_thesis, remaining_total)
            floor = self._min_topup_fraction * requested_usd
            if granted <= 0 or granted < floor:
                binding = "thesis" if remaining_thesis <= remaining_total else "total"
                intents[sc.id] = (
                    "thesis_already_funded" if binding == "thesis"
                    else "risk_cap_total"
                )
                auction.append({
                    **record,
                    "granted_risk_usd": 0.0,
                    "outcome": intents[sc.id],
                })
                continue
            if (
                remaining_symbol_notional is not None
                and remaining_symbol_notional <= 0
            ):
                intents[sc.id] = "notional_cap_symbol"
                auction.append({
                    **record,
                    "granted_risk_usd": 0.0,
                    "outcome": "notional_cap_symbol",
                })
                continue
            position_id = self._next_position_id(account, sc.id)
            signal = plan["signal"]
            dynamic_exit = None
            if sc.dynamic_exit is not None and direction == "long":
                dynamic_exit = DynamicExitState.initial(
                    sc.dynamic_exit,
                    entry_price=plan["execution_price"],
                    last_candle_ts=(
                        plan["monitor_candle"].get("ts")
                        if plan.get("monitor_candle") is not None else None
                    ),
                    initial_target_price=signal.suggested_take_profit,
                    atr_risk=plan["atr_risk"],
                ).to_dict()
            fill = self._broker.open(
                account,
                strategy_id=sc.id,
                position_id=position_id,
                symbol=sc.symbol,
                side=direction,
                price=plan["execution_price"],
                atr_risk=plan["atr_risk"],
                risk_distance=request["risk_distance"],
                risk_pct=granted / equity_for_sizing,
                equity_for_sizing=equity_for_sizing,
                ts=ts,
                entry_reason=signal.entry_reason,
                exit_policy=sc.exit_policy,
                monitor_timeframe=sc.monitor_timeframe,
                stop_loss_price=signal.suggested_stop,
                take_profit_price=signal.suggested_take_profit,
                sl_basis=(
                    "strategy_suggested"
                    if signal.suggested_stop is not None else ""
                ),
                reward_risk_ratio=sc.reward_risk_ratio,
                max_leverage=self._max_leverage,
                max_notional_usd=remaining_symbol_notional,
                risk_sizing_basis=self._risk_sizing_basis,
                dynamic_exit=dynamic_exit,
            )
            if (
                fill
                and position_id in account.positions
                and plan.get("monitor_candle") is not None
            ):
                account.positions[position_id].last_monitor_candle_ts = (
                    plan["monitor_candle"].get("ts")
                )
            if fill:
                fills.append(fill)
                if fill.get("dynamic_exit") is not None:
                    fill["fill_id"] = self._dynamic_id(
                        "fill", fill["action"], fill["position_id"],
                        plan["candle_ts"], "open",
                    )
                account.positions[position_id].execution_method = self._execution_mode + "_v2"
                executed_risk = account.positions[position_id].budget_risk_usd
                fill.update({
                    "execution_method": self._execution_mode + "_v2",
                    "signal_candle_ts": plan["candle_ts"],
                    "signal_available_ts": plan["candle_ts"] + _TIMEFRAME_MS[sc.timeframe],
                    "price_asof_ts": (plan["execution_candle_ts"] if self._execution_mode == "next_open" else plan["price_asof_ts"]),
                    "decision_observed_at": ts,
                    "authorized_risk_usd": granted,
                    "executed_risk_usd": executed_risk,
                })
                outcome = "granted_full" if executed_risk >= requested_usd - 1e-8 else "granted_topup"
                intents[sc.id] = (
                    "reverse_open" if reversing
                    else "open" if not same_direction else "open_topup"
                )
            else:
                outcome = "broker_noop"
                intents[sc.id] = "hold"
            if fill and self._execution_mode == "next_open":
                entry_cursor = plan["execution_candle_ts"] - _TIMEFRAME_MS[sc.monitor_timeframe or sc.timeframe]
                position = account.positions[position_id]
                position.last_monitor_candle_ts = entry_cursor
                if position.dynamic_exit is not None:
                    position.dynamic_exit["last_candle_ts"] = entry_cursor
            auction.append({
                **record,
                "authorized_risk_usd": round(granted, 6),
                "executed_risk_usd": round(executed_risk if fill else 0.0, 6),
                "granted_risk_usd": round(executed_risk if fill else 0.0, 6),
                "grant_fraction": (
                    round((executed_risk if fill else 0.0) / requested_usd, 6) if requested_usd else None
                ),
                "position_id": position_id,
                "tranche_key": position_id,
                "outcome": outcome,
            })

        if self._execution_mode == "next_open" and not ambiguous_replay:
            post_intents, post_fills, _ = self._monitor_plans(account, plans, ts, errors, historical=True)
            fills.extend(post_fills)
            intents.update(post_intents)
            for plan in plans:
                if plan.get("monitor_candles"):
                    prices[plan["sc"].symbol] = float(plan["monitor_candles"][-1]["close"])

        account.last_event_ts = max(account.last_event_ts or 0, observed_at_ms)
        equity_after = account.equity(prices)
        if fills:
            self._commit_fills(account, fills)
        else:
            self._save_account(account)
        per_strategy: dict[str, float] = {}
        for position in account.positions.values():
            if position.symbol not in prices:
                continue
            per_strategy[position.strategy_id] = round(
                per_strategy.get(position.strategy_id, 0.0)
                + position.unrealized_usd(prices[position.symbol]),
                4,
            )
        if not unpriced_symbols:
            self._append(self._equity_path, {
                "ts": ts,
                "equity_usd": round(equity_after, 4),
                "balance_usd": round(account.balance_usd, 4),
                "open_positions": len(account.positions),
                "unrealized_by_strategy": per_strategy,
            })
        return {
            "ts": ts,
            "equity_usd": equity_after,
            "balance_usd": account.balance_usd,
            "intents": intents,
            "fills": fills,
            "errors": errors,
            "open_positions": list(account.positions),
            "auction": auction,
            "execution_method": self._execution_mode + "_v2",
            "valuation_complete": not unpriced_symbols,
            "marks": prices,
            "strategies": {plan["sc"].id: {"signal_candle_ts": plan.get("candle_ts"), "monitor_candle_ts": (plan.get("monitor_candles") or [{}])[-1].get("ts"), "intent": intents.get(plan["sc"].id), "errors": {key: value for key, value in errors.items() if key.startswith(plan["sc"].id)}} for plan in plans},
            "gross_notional_usd": sum(position.qty * prices.get(position.symbol, position.entry_px) for position in account.positions.values()),
            "leverage_cap_scope": "per_tranche",
            "max_leverage_per_tranche": self._max_leverage,
            "entry_drawdown": entry_drawdown,
            "entry_drawdown_halt": drawdown_halt,
            "entry_drawdown_multiplier": entry_drawdown_multiplier,
        }
