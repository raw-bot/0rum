---
plan: 03-03
phase: 03-strategy-engine
status: complete
commit: 618a39b
---

## What was built

Two pure signal-generator strategy classes completing the Phase 3 strategy library:

- **BreakoutExpansionStrategy** — detects H4 consolidation ranges (range_width < 1.5×ATR14), confirms H1 breakout close outside range with volume > volume_mult×SMA20, computes linear confidence (squeeze duration 35%, volume ratio 45%, range clarity 20%), returns CandidateSignal with TP=1×range_width (FIXED) and TP2=None.

- **EmaMomentumStrategy** — detects H1 EMA(fast) crossing EMA(slow) with EMA(50) trend filter, validates fast_ema < slow_ema param constraint, computes linear confidence (crossover angle 40%, EMA50 distance 35%, trend alignment 25%), returns CandidateSignal with TP=1.5×risk (FIXED) and TP2=None.

Both: no DB writes, no APScheduler, async generate_signals(), structlog, full type hints, Google docstrings, clamped confidence [0.0, 1.0] per D-03.

## Artifacts

- `src/strategies/breakout_expansion.py` — BreakoutExpansionStrategy (PARAM_RANGES: squeeze_lookback 12-30, volume_mult 1.2-2.5, sl_atr_mult 0.5-1.5)
- `src/strategies/ema_momentum.py` — EmaMomentumStrategy (PARAM_RANGES: fast_ema 5-15, slow_ema 15-30, sl_atr_mult 0.2-0.8)
- `tests/test_strategies/test_breakout_expansion.py` — 14 unit tests
- `tests/test_strategies/test_ema_momentum.py` — 21 unit tests

## Verification

```
35 passed in 0.73s
```

All 35 tests pass. Key checks confirmed:
- PARAM_RANGES: exactly 3 keys per strategy
- Confidence weights sum to 1.00 (0.35+0.45+0.20 and 0.40+0.35+0.25)
- TP1 = entry + range_width (Breakout), TP1 = entry + 1.5×risk (EMA Momentum)
- TP2 = None in both strategies
- No RSI in BreakoutExpansion, no MACD in EmaMomentum (AST-verified)
- No SQLAlchemy session references
- No APScheduler references
- fast_ema >= slow_ema guard returns [] with WARNING log (T-03-09 mitigated)
- Volume=0 guard via max(sma20_volume, 1) (T-03-12 mitigated)

## Notes

- The `grep -i "macd|sigmoid|rsi"` verification command hits docstring comments that explain why those indicators are excluded — this is expected. The AST-based tests in the test suite confirm no actual code usage of forbidden identifiers.
- `_ema()` helper is duplicated in EmaMomentumStrategy (not imported from other strategies) per phase design decisions to keep strategies decoupled.
- EMA(50) period is a module-level constant `_EMA50_PERIOD = 50` and TP ratio is `_TP_RISK_RATIO = 1.5` — both structural and not in PARAM_RANGES per CLAUDE.md §17.
- Volume SMA window uses h1_candles[-22:-2] (20 candles before the breakout window) to avoid look-ahead bias.
