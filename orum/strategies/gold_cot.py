"""Gold COT-gate engine (long-only), adapted to the `StrategyEngine` contract.

The gold strategy is a CONTINUATION play on positioning extremes: hold a long
while the commercial COT-index sits at/below the threshold (commercials max
short == specs max long), flat otherwise. This is the runtime distillation of
`scripts/cot_gated_gold.py` / `scripts/portfolio_shadow.gold_cot`.

The gate value is a weekly, gold-specific datum. Per the project's isolation
rule it is NOT fetched here: a separate updater writes `state/cot_gate.json`
and this engine only READS it, through `orum.strategies.cot_gate` — the single
module that knows the COT cache format. BTC (`ak_macd`) and ETH (`donchian`)
never import that module and never see this data.

Decision on each closed candle:
  gate unavailable / invalid / stale -> None      (no-trade; never fabricated)
  gate_on  == True                   -> LONG      (continue/enter the long)
  gate_on  == False                  -> EXIT      (positioning no longer extreme)

Like every engine it is position-agnostic (base.py): it reports what the gate
says every candle; the paper broker turns a repeated LONG-while-already-long or
EXIT-while-flat into a no-op. A missing gate returns None, so it can only ever
*stop* gold from trading — it never force-closes on absent data and never
touches the other strategies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from orum.paths import COT_GATE_PATH
from orum.strategies.base import Side, Signal, StrategyContext
from orum.strategies.cot_gate import DEFAULT_MAX_AGE_DAYS, read_gate


class GoldCotEngine:
    name = "gold_cot"
    version = "1"
    required_timeframes = ["1d"]
    required_indicators = ["cot_gate"]  # supplied via cache, not computed from candles

    def __init__(self) -> None:
        self._gate_path = str(COT_GATE_PATH)
        self._max_age_days = DEFAULT_MAX_AGE_DAYS
        self._gate_reference_time = None
        self.warmup_period = 0  # the decision is the gate, not candle history

    def init(self, config: dict) -> None:
        """`config` is the engine's `params` block from `goal.yaml`. Recognised
        keys: `gate_path` (override the cache location, e.g. for tests) and
        `max_age_days` (staleness tolerance). Bad values fall back to the
        defaults rather than raising, matching the other engines."""
        cfg = config if isinstance(config, dict) else {}
        gate_path = cfg.get("gate_path")
        if isinstance(gate_path, str) and gate_path:
            self._gate_path = gate_path
        reference = cfg.get("gate_reference_time")
        self._gate_reference_time = datetime.fromisoformat(reference) if reference else None
        if self._gate_reference_time is not None and self._gate_reference_time.tzinfo is None:
            raise ValueError("gate_reference_time must be timezone aware")
        max_age = cfg.get("max_age_days")
        if isinstance(max_age, (int, float)) and not isinstance(max_age, bool) and max_age > 0:
            self._max_age_days = float(max_age)

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        duration_ms = {"1d": 86_400_000, "4h": 14_400_000, "1h": 3_600_000}.get(context.timeframe)
        if duration_ms is None:
            return None
        asof = datetime.fromtimestamp((float(candle["ts"]) + duration_ms) / 1000, timezone.utc)
        clock = {"now": self._gate_reference_time} if self._gate_reference_time is not None else {}
        gate = read_gate(self._gate_path, max_age_days=self._max_age_days, asof=asof, **clock)
        self.data_error = "cot_gate_unavailable_or_stale" if gate is None else None
        if gate is None:
            return None  # cache absent / malformed / stale -> no-trade, never invented

        meta = {
            "cot_index": gate.cot_index,
            "threshold": gate.threshold,
            "report_date": gate.report_date,
            "usable_from": gate.usable_from,
        }
        if gate.gate_on:
            return Signal(
                side=Side.LONG,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason=f"COT gate ON (index {gate.cot_index} <= {gate.threshold}, report {gate.report_date})",
                strategy_metadata=meta,
            )
        return Signal(
            side=Side.EXIT,
            symbol=context.symbol,
            timeframe=context.timeframe,
            entry_reason=f"COT gate OFF (index {gate.cot_index} > {gate.threshold}, report {gate.report_date})",
            strategy_metadata=meta,
        )


# Lets the registry (orum/strategies/__init__.py) load this engine by module
# path, not just by built-in name.
ENGINE_CLASS = GoldCotEngine
