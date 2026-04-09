# Phase 4: Signal Pipeline - Context

**Gathered:** 2026-04-09
**Status:** Ready for planning

<domain>
## Phase Boundary

Receive `list[CandidateSignal]` from StrategyRunner, run through dedup → conflict filter → regime detection → ranker → quota gate, then batch-persist all candidates (with final statuses) and approved signals to the DB. Delivers `ApprovedSignalORM` rows ready for Phase 6 risk gates and Phase 7 execution.

No risk gate logic, no Telegram sending, no trade tracking — those belong to Phases 6 and 7.

</domain>

<decisions>
## Implementation Decisions

### Pipeline Trigger & Scheduling
- **D-01:** A single APScheduler job runs every 15 minutes (aligned with M15 candle refresh). It calls StrategyRunner first, then passes the resulting `list[CandidateSignal]` directly to `PipelineRunner` — one inline call, no separate pipeline job, no Redis queue.

### WFE Fallback
- **D-02:** When `optimizer_results` has no active params for a strategy (Phase 5 has not run yet), `strategy_recent_wfe` defaults to **0.5** — the WFE gate minimum. This treats "no data" as neutral: no bonus, no penalty. Logged via structlog at INFO level when fallback is used.

### Strategy-Regime Alignment Map
- **D-03:** The `regime_alignment` factor (1.0 if aligned, 0.5 otherwise) uses this fixed map:

  | Strategy | Aligned regimes (score 1.0) |
  |---|---|
  | `liquidity_sweep` | RANGING |
  | `trend_continuation` | TRENDING_UP, TRENDING_DOWN |
  | `breakout_expansion` | TRENDING_UP, TRENDING_DOWN, HIGH_VOL |
  | `ema_momentum` | TRENDING_UP, TRENDING_DOWN |

  All other strategy-regime combinations → 0.5. Map is hardcoded as a module-level constant in `ranker.py`.

### Candidate Signal Persistence
- **D-04:** The pipeline processes all signals entirely in memory. At the end, a single DB transaction writes:
  1. All `CandidateSignalORM` rows with their final status (`PENDING`→`DEDUPED`, `REJECTED`, or `APPROVED`)
  2. All `ApprovedSignalORM` rows for signals that passed the quota gate
  This gives full audit trail (every filtered signal visible in DB) with minimal round-trips.

### Claude's Discretion
- Internal structure of `PipelineRunner` (whether each step is a method or standalone function)
- How the 15-min job is named and wired into the existing `create_scheduler()` in `src/scheduler/jobs.py`
- Whether regime detection is a method on `PipelineRunner` or a standalone `RegimeDetector` class called by it
- Unit test fixture design (frozen `list[CandidateSignal]` inputs, mocked DB — same pattern as Phase 3)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pipeline Logic (step-by-step rules, formulas, thresholds)
- `CLAUDE.md` §10 — Full signal pipeline spec: dedup (§10.1), conflict filter (§10.2), ranking formula (§10.3), quota gate (§10.4)
- `CLAUDE.md` §11.4 — Regime detection rules: ADX(14) + ATR percentile → TRENDING_UP/DOWN/RANGING/HIGH_VOL

### Data Models
- `src/models/signal_data.py` — `CandidateSignal`, `ApprovedSignal`, `MarketRegime`, `MarketRegimeType`, `SignalStatus` Pydantic classes
- `src/models/signal.py` — `CandidateSignalORM`, `ApprovedSignalORM` ORM models
- `src/models/regime.py` — `MarketRegimeORM` ORM model

### Integration Points
- `src/strategies/runner.py` — StrategyRunner output (`list[CandidateSignal]`) is pipeline input
- `src/scheduler/jobs.py` — `create_scheduler()` where new 15-min pipeline job is added
- `src/models/optimizer_result.py` — `OptimizerResultORM` with `wfe` and `is_active` fields (source of `strategy_recent_wfe`)
- `src/database.py` — `AsyncSessionLocal` for batch DB write at pipeline end

### Project Constraints
- `CLAUDE.md` §2 — Tech stack (Python 3.12, async everywhere, Pydantic v2, SQLAlchemy 2.0 async)
- `CLAUDE.md` §5 — `MAX_SIGNALS_PER_DAY=5` env var (quota gate limit)
- `.planning/PROJECT.md` — Core value: signal quality and capital protection are non-negotiable

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/models/signal.py`: `CandidateSignalORM` + `ApprovedSignalORM` fully defined — batch write at end maps from Pydantic → ORM
- `src/models/regime.py`: `MarketRegimeORM` fully defined — regime detector writes here before ranker reads latest regime
- `src/models/signal_data.py`: `SignalStatus` enum has PENDING/APPROVED/REJECTED/DEDUPED — all statuses pipeline needs
- `src/strategies/runner.py`: StrategyRunner already exists and returns `list[CandidateSignal]` — pipeline receives this directly
- `src/database.py`: `AsyncSessionLocal` — use same `async with AsyncSessionLocal() as session` pattern for batch write

### Established Patterns
- Async everywhere: all DB operations use `async with AsyncSessionLocal() as session`
- structlog: `log = structlog.get_logger(__name__)` at module level, structured key=value events
- APScheduler jobs: see `src/scheduler/jobs.py` `create_scheduler()` — add 15-min interval job following existing IntervalTrigger pattern
- In-memory Pydantic objects flow through the pipeline; ORM mapping happens only at persistence boundary

### Integration Points
- New `src/pipeline/` package (dedup.py, conflict_filter.py, ranker.py, quota.py + runner.py coordinator)
- 15-min APScheduler job added to `create_scheduler()` in `src/scheduler/jobs.py`
- `src/backtesting/regime_detector.py` — regime detection lives here per project structure (CLAUDE.md §4); pipeline calls it inline

</code_context>

<specifics>
## Specific Ideas

No specific "I want it like X" references — open to standard approaches within the constraints above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 04-signal-pipeline*
*Context gathered: 2026-04-09*
