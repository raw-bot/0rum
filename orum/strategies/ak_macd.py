"""Adapts `orum.external.ak_macd` (the AK MACD 15m brain) to the
`StrategyEngine` contract. No signal-generation logic is duplicated or
changed: `on_candle` is a thin translation from `AkMacdVerdict` to `Signal`.

Not wired into the live pipeline yet. `ak_macd_producer.py` still calls
`evaluate_ak_macd_verdict` directly and routes confirmed candidates through
`orchestrator.py`; this wrapper exists so a future cutover has something
proven equivalent. `test_ak_macd_engine_parity.py` pins that equivalence.

Only `BUY_CANDIDATE`/`SELL_CANDIDATE` ever carry a payload today (see
`run_candidate_machine`); ak_macd never emits its own EXIT -- exits stay the
native risk layer's job (stop/TP/max_hold), unchanged by this wrapper.

`init()`'s override validation (which keys are accepted, default fallback on
bad/out-of-range values) intentionally mirrors
`ak_macd_producer.load_ak_macd_params` exactly, since both read the same
`ak_macd:` section of `state/strategy.yaml`. They are not unified into one
function yet because `ak_macd_producer.py` isn't touched until the cutover
commit; unify then.
"""

from __future__ import annotations

from dataclasses import replace

from orum.external.ak_macd import (
    DEFAULT_STRATEGY_ID,
    AkMacdParams,
    evaluate_ak_macd_verdict,
)
from orum.strategies.base import Side, Signal, StrategyContext

_EVENT_SIDE = {"BUY_CANDIDATE": Side.LONG, "SELL_CANDIDATE": Side.SHORT}


def _pos_int(cfg: dict, key: str, default: int) -> int:
    value = cfg.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else default


def _bool(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    return value if isinstance(value, bool) else default


class AkMacdEngine:
    name = "ak_macd"
    version = DEFAULT_STRATEGY_ID
    required_timeframes = ["15m"]
    required_indicators = ["macd", "ema_baseline", "atr", "volume_sma"]

    def __init__(self) -> None:
        self._params = AkMacdParams()
        self.warmup_period = self._params.warmup

    def init(self, config: dict) -> None:
        """`config` is the `ak_macd:` section of `state/strategy.yaml` (or an
        empty dict). Unknown or out-of-range keys are ignored in favour of
        the `AkMacdParams` default -- never a hard error on a malformed
        config, matching the existing producer's behaviour."""
        cfg = config if isinstance(config, dict) else {}
        defaults = AkMacdParams()
        self._params = replace(
            defaults,
            confirmation_bars=_pos_int(cfg, "confirmation_bars", defaults.confirmation_bars),
            candidate_window_bars=_pos_int(cfg, "candidate_window_bars", defaults.candidate_window_bars),
            regime_filter=_bool(cfg, "regime_filter", defaults.regime_filter),
            require_candle_direction=_bool(cfg, "require_candle_direction", defaults.require_candle_direction),
        )
        self.warmup_period = self._params.warmup

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        verdict = evaluate_ak_macd_verdict(
            context.candles, self._params, symbol=context.symbol, timeframe=context.timeframe,
        )
        if verdict.payload is None or verdict.event not in _EVENT_SIDE:
            return None
        return Signal(
            side=_EVENT_SIDE[verdict.event],
            symbol=context.symbol,
            timeframe=context.timeframe,
            entry_reason=verdict.reason,
            strategy_metadata={
                "verdict_action": verdict.action,
                "macd": verdict.macd,
                "regime": verdict.regime,
                "rolling_return": verdict.rolling_return,
                "payload": verdict.payload,
            },
        )


# Lets the registry (orum/strategies/__init__.py) load this engine by module
# path, not just by built-in name.
ENGINE_CLASS = AkMacdEngine
