# Phase 3: Strategy Engine - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver 4 independent technical strategy modules (`liquidity_sweep`, `trend_continuation`, `breakout_expansion`, `ema_momentum`) plus a `StrategyRunner` coordinator. Each strategy takes candle data in and returns in-memory `CandidateSignal` objects. No DB writes, no scheduler wiring — pure signal generation logic that Phase 4 will consume.

</domain>

<decisions>
## Implementation Decisions

### Confidence Scoring
- **D-01:** Each strategy computes confidence as a **weighted sum** of its quality factors (the ones listed in CLAUDE.md §9.2–9.5 per strategy).
- **D-02:** Weights are **per-strategy** — Claude assigns weights that match each strategy's signal nature (e.g., volume matters more for Breakout Expansion than for EMA Momentum). Weights must be documented in code comments.
- **D-03:** Result is clamped to [0.0, 1.0].

### Strategy Runner
- **D-04:** A `StrategyRunner` class lives at `src/strategies/runner.py`. It loads active params from DB, calls all 4 strategies via `asyncio.gather()`, and returns the combined `list[CandidateSignal]`.
- **D-05:** If a strategy has no active params in `optimizer_results` (optimizer hasn't run yet), StrategyRunner falls back to **hardcoded midpoint defaults** for that strategy's `PARAM_RANGES`. Strategies always produce signals. Fallback is logged via structlog at INFO level.
- **D-06:** StrategyRunner loads **500 candles per timeframe** (M15, H1, H4, D1) from the DB for each run. Uniform window — sufficient for EMA(200), swing detection (order=10), and ATR(14) with headroom.

### DB Persistence Scope
- **D-07:** Strategies are **pure functions** — they return `list[CandidateSignal]` in memory only. No DB writes inside strategy classes or StrategyRunner. The signal pipeline (Phase 4) owns persistence to `candidate_signals`.

### Scheduler Wiring
- **D-08:** Phase 3 does **not** add any APScheduler jobs. It delivers strategy modules and StrategyRunner only. Scheduling is Phase 4's responsibility.

### Claude's Discretion
- Exact confidence weights per strategy (within the per-strategy weighted-sum constraint)
- Internal helper methods on each strategy class (e.g., swing detection, EMA calculation utilities)
- How argrelextrema results are cached or reused within a single `generate_signals()` call
- Whether strategies share a common `IndicatorMixin` or each implement their own ATR/EMA helpers

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Strategy Specs (logic, parameters, fixed values)
- `CLAUDE.md` §9 — Full strategy engine spec: AbstractStrategy base (§9.1), Liquidity Sweep (§9.2), Trend Continuation (§9.3), Breakout Expansion (§9.4), EMA Momentum (§9.5)
- `CLAUDE.md` §7 — Pydantic data classes: `CandidateSignal`, `CandleData`, `StrategyName`, `Direction`, `Timeframe`

### Existing Code
- `src/models/signal.py` — `CandidateSignalORM` and `ApprovedSignalORM` ORM models
- `src/models/candle.py` — `Candle` ORM model (source of candle data)
- `src/models/optimizer_result.py` — `OptimizerResultORM` (source of active params)
- `src/database.py` — `AsyncSessionLocal` for DB access in StrategyRunner

### Project Constraints
- `CLAUDE.md` §2 — Tech stack (Python 3.12, async everywhere, Pydantic v2, SQLAlchemy 2.0 async)
- `.planning/PROJECT.md` — Out of scope: no ML, no RSI/MACD, max 3 optimizable params per strategy

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/models/signal.py`: `CandidateSignalORM` already defined — strategies produce in-memory `CandidateSignal` (Pydantic), pipeline maps to ORM
- `src/models/optimizer_result.py`: `OptimizerResultORM` with `is_active` flag — StrategyRunner queries `WHERE is_active = TRUE AND strategy = ?`
- `src/models/candle.py`: `Candle` ORM with `timeframe` and `timestamp` indexed — efficient 500-candle window queries
- `src/database.py`: `AsyncSessionLocal` available for StrategyRunner DB queries
- `src/ingestion/candle_fetcher.py`: pattern for async DB reads (upsert/query via `async with AsyncSessionLocal()`) — reuse this pattern in StrategyRunner

### Established Patterns
- Async everywhere: all DB operations use `async with AsyncSessionLocal() as session`
- Pydantic v2 data classes: all signal data flows as `CandidateSignal` (not ORM objects) until persistence
- structlog: `log = structlog.get_logger(__name__)` at module level, structured key=value events
- `scipy.argrelextrema` already in stack for swing detection (Liquidity Sweep, Trend Continuation)

### Integration Points
- StrategyRunner output (`list[CandidateSignal]`) is the direct input to the Phase 4 signal pipeline
- `OptimizerResultORM` is the source of active params — StrategyRunner reads this table
- `Candle` table is the source of candle data — StrategyRunner reads last 500 rows per timeframe

</code_context>

<specifics>
## Specific Ideas

No specific "I want it like X" references from discussion — open to standard approaches within the constraints above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-strategy-engine*
*Context gathered: 2026-04-08*
