"""Donchian channel breakout engine (long-only), adapted to the
`StrategyEngine` contract.

This is the ETH engine of the multi-asset paper portfolio, but it is
asset-agnostic: it decides purely from the candle closes in
`StrategyContext`, so the same class drives any symbol the registry points at
it (the research harness `scripts/donchian_strategy.py` also runs it on BTC).

Signal, on the last CLOSED candle (`candles[-1]`):
  entry (LONG) : close > highest of the prior `entry_n` closes  -> breakout up
  exit  (EXIT) : close < lowest  of the prior `exit_n`  closes  -> channel break

This is the exact "current state" logic the dashboard overlay
(`dashboard._donchian_markers`) and the shadow poller
(`scripts/portfolio_shadow.donchian_state`) already draw, so the chart, the
paper fills and this engine all tell one story. No short side: the portfolio
is long-only (`goal.yaml: allow_short = false`); a break of the entry channel
downward simply produces no signal, never a SHORT.

Like every engine, `on_candle` has no notion of an open position (kept out of
`StrategyContext` on purpose, see base.py): it always reports what the channel
says and returns EXIT before LONG when a (degenerate) candle would trip both.
Acting on EXIT with nothing open is a caller-side no-op.
"""

from __future__ import annotations

from orum.strategies.base import Side, Signal, StrategyContext

_DEFAULT_ENTRY_N = 20
_DEFAULT_EXIT_N = 10


def _pos_int(cfg: dict, key: str, default: int) -> int:
    value = cfg.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else default


class DonchianEngine:
    name = "donchian"
    version = "1"
    required_timeframes = ["1d"]
    required_indicators = ["donchian_high", "donchian_low"]

    def __init__(self) -> None:
        self._entry_n = _DEFAULT_ENTRY_N
        self._exit_n = _DEFAULT_EXIT_N
        self.warmup_period = _DEFAULT_ENTRY_N + 1

    def init(self, config: dict) -> None:
        """`config` is the engine's `params` block from `goal.yaml`. Unknown or
        out-of-range keys fall back to the Donchian 20/10 default rather than
        raising, matching `AkMacdEngine.init`."""
        cfg = config if isinstance(config, dict) else {}
        self._entry_n = _pos_int(cfg, "entry_n", _DEFAULT_ENTRY_N)
        self._exit_n = _pos_int(cfg, "exit_n", _DEFAULT_EXIT_N)
        # Need `entry_n` prior closes plus the current one to judge a breakout.
        self.warmup_period = self._entry_n + 1

    def on_candle(self, candle: dict, context: StrategyContext) -> Signal | None:
        closes = [c["close"] for c in context.candles]
        if len(closes) < self._entry_n + 1:
            return None  # not enough history to form the entry channel yet

        current = closes[-1]
        prior_high = max(closes[-self._entry_n - 1:-1])
        prior_low = min(closes[-self._exit_n - 1:-1]) if len(closes) > self._exit_n else min(closes[:-1])

        if current < prior_low:
            return Signal(
                side=Side.EXIT,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason=f"close {current:.4g} < {self._exit_n}-bar channel low {prior_low:.4g}",
                strategy_metadata={"channel_low": prior_low, "exit_n": self._exit_n},
            )
        if current > prior_high:
            return Signal(
                side=Side.LONG,
                symbol=context.symbol,
                timeframe=context.timeframe,
                entry_reason=f"close {current:.4g} > {self._entry_n}-bar channel high {prior_high:.4g}",
                strategy_metadata={"channel_high": prior_high, "entry_n": self._entry_n},
            )
        return None


# Lets the registry (orum/strategies/__init__.py) load this engine by module
# path, not just by built-in name.
ENGINE_CLASS = DonchianEngine
