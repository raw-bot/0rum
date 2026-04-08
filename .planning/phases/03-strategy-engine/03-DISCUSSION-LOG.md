# Phase 3: Strategy Engine - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-08
**Phase:** 03-strategy-engine
**Areas discussed:** Confidence scoring, Strategy runner, DB persistence scope, Scheduler wiring

---

## Confidence Scoring

| Option | Description | Selected |
|--------|-------------|----------|
| Simple weighted sum | Each factor gets a weight, clamp to [0,1]. Transparent and tunable. | ✓ |
| Normalized sub-scores averaged | Normalize each factor to [0,1] independently, then average. Equal weights. | |
| You decide per-strategy | Claude chooses an appropriate formula for each strategy. | |

**User's choice:** Simple weighted sum

---

| Option | Description | Selected |
|--------|-------------|----------|
| Claude decides per-strategy | Weights that match each strategy's signal nature. Documented in code. | ✓ |
| Uniform weights across all | Equal weight for all factors within each strategy. | |

**User's choice:** Claude decides per-strategy

---

## Strategy Runner

| Option | Description | Selected |
|--------|-------------|----------|
| StrategyRunner class | src/strategies/runner.py, asyncio.gather(), returns list[CandidateSignal]. | ✓ |
| Direct calls in pipeline | Phase 4 calls each strategy directly — no runner abstraction. | |
| You decide | Claude chooses the coordination pattern. | |

**User's choice:** StrategyRunner class

---

| Option | Description | Selected |
|--------|-------------|----------|
| Use midpoint defaults | Hardcoded default params at midpoint of PARAM_RANGES as fallback. | ✓ |
| Skip the strategy silently | Omit strategy from run if no active params. | |
| Raise an error / alert | Treat missing params as config error. | |

**User's choice:** Use midpoint defaults

---

| Option | Description | Selected |
|--------|-------------|----------|
| 500 candles per timeframe | Fixed window, sufficient for EMA(200) and all indicators. | ✓ |
| Strategy declares its own window | Each strategy declares CANDLE_REQUIREMENTS dict. | |
| You decide | Claude determines from worst-case indicator needs. | |

**User's choice:** 500 candles per timeframe

---

## DB Persistence Scope

| Option | Description | Selected |
|--------|-------------|----------|
| In-memory only — Phase 4 writes to DB | Strategies return list[CandidateSignal], pipeline owns persistence. | ✓ |
| Strategies write to DB directly | StrategyRunner saves to candidate_signals table after generation. | |

**User's choice:** In-memory only — Phase 4 writes to DB

---

## Scheduler Wiring

| Option | Description | Selected |
|--------|-------------|----------|
| Modules only — no scheduler | Phase 3 delivers StrategyRunner and strategy classes only. | ✓ |
| Phase 3 includes scheduler wiring | APScheduler job added to src/scheduler/jobs.py in Phase 3. | |

**User's choice:** Modules only — no scheduler

---

## Claude's Discretion

- Exact confidence weights per strategy
- Internal helper methods (EMA, ATR, swing detection utilities)
- Whether strategies share a common mixin or each implement their own helpers

## Deferred Ideas

None.
