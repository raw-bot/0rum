---
plan: 03-02
phase: 03-strategy-engine
status: complete
commit: f6d0b1f
---

## What was built

LiquiditySweepStrategy and TrendContinuationStrategy — pure in-memory signal generators
with no DB writes. Both subclass AbstractStrategy, define exactly 3 PARAM_RANGES keys,
and compute confidence as a documented linear weighted sum (no sigmoid, no exp/log).

LiquiditySweepStrategy detects M15 candles whose wick pierces an H4 swing level by
> sweep_atr_mult×ATR14 while the close returns inside, then calculates entry/SL/TP with
linear confidence from sweep depth (40%), volume spike (35%), and level proximity (25%).

TrendContinuationStrategy filters trend direction via structural EMA(50)>EMA(200) on H1,
detects pullback touches to EMA(pullback_ema) in the last 5 H1 candles, confirms via M15
price action (engulfing, pin bar, inside bar breakout), and computes confidence from
ADX-based trend strength (45%), pullback EMA proximity quality (35%), and PA pattern
score (20%). TP2 uses the nearest H1 swing high/low beyond TP1; falls back to TP1×1.5
if no qualifying swing is found.

## Artifacts

- `src/strategies/liquidity_sweep.py` — LiquiditySweepStrategy
- `src/strategies/trend_continuation.py` — TrendContinuationStrategy
- `tests/test_strategies/test_liquidity_sweep.py` — 13 tests
- `tests/test_strategies/test_trend_continuation.py` — 11 tests

## Verification

```
24 passed in 0.70s
```

All forbidden-pattern checks passed:
- No `sigmoid`, `np.exp`, `math.exp` calls in either strategy file (only in comments)
- No `session`, `AsyncSession` imports or calls
- No APScheduler references

```
python -c "from src.strategies.liquidity_sweep import LiquiditySweepStrategy; print(LiquiditySweepStrategy.PARAM_RANGES)"
{'sweep_atr_mult': (0.2, 0.8), 'sl_atr_mult': (0.3, 1.0), 'tp_risk_mult': (1.2, 3.0)}

python -c "from src.strategies.trend_continuation import TrendContinuationStrategy; print(TrendContinuationStrategy.PARAM_RANGES)"
{'pullback_ema': (15.0, 55.0), 'sl_atr_mult': (0.3, 1.0), 'tp_risk_mult': (1.2, 3.0)}
```

## Notes

- Test fixtures use `SimpleNamespace` with `Decimal` attributes matching the Candle ORM
  interface — no live DB required.
- Swing level detection uses the actual detected level (not a hard-coded price) to
  ensure sweep candle geometry is consistent with argrelextrema output.
- TrendContinuationStrategy._ema() seeds with SMA of first `period` values (alpha=2/(period+1)),
  matching the spec's EWM convention.
- _compute_adx() returns 0.0 when fewer than 2×period candles are available, which
  feeds a trend_strength_norm of 0.0 into the confidence score (conservative fallback).
- Missing params in __init__ are filled with PARAM_RANGES midpoints and logged via
  structlog (T-03-05 threat mitigation).
- The `prev_range_size` module-level function was removed after the inside-bar
  detection logic was inlined cleanly into `_detect_pa_pattern`.

## Self-Check: PASSED

- `src/strategies/liquidity_sweep.py` exists: FOUND
- `src/strategies/trend_continuation.py` exists: FOUND
- `tests/test_strategies/test_liquidity_sweep.py` exists: FOUND
- `tests/test_strategies/test_trend_continuation.py` exists: FOUND
- Commit f6d0b1f exists: FOUND
