# Phase 4: Signal Pipeline - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-09
**Phase:** 04-signal-pipeline
**Areas discussed:** Pipeline trigger & cadence, WFE fallback value, Strategy-regime alignment map, CandidateSignal persistence

---

## Pipeline Trigger & Cadence

| Option | Description | Selected |
|--------|-------------|----------|
| A — Inline, 15 min | StrategyRunner → PipelineRunner in one APScheduler job, every 15 min | ✓ |
| B — Separate scheduled job | StrategyRunner writes to DB, pipeline job reads pending candidates independently | |
| C — Event-driven (Redis queue) | StrategyRunner emits to Redis, pipeline consumes | |

**User pick:** A — inline, every 15 min

---

## WFE Fallback Value

| Option | Description | Selected |
|--------|-------------|----------|
| A — 0.5 | Neutral: treats "no data" as barely passing the WFE gate minimum | ✓ |
| B — 0.0 | Conservative: penalizes unvalidated strategies | |
| C — 1.0 | Optimistic: maximum benefit of the doubt for unvalidated strategies | |

**User pick:** A — 0.5

---

## Strategy-Regime Alignment Map

| Option | Description | Selected |
|--------|-------------|----------|
| Proposed base map | liquidity_sweep→RANGING, trend_continuation/ema_momentum→TRENDING_*, breakout_expansion→TRENDING_*+HIGH_VOL | ✓ |
| HIGH_VOL: no strategy aligned | All strategies get 0.5 in HIGH_VOL | |
| HIGH_VOL: Breakout Expansion aligned | Breakout Expansion gets 1.0 in HIGH_VOL (chosen) | ✓ |

**User pick:** Confirmed base map + Option B for HIGH_VOL (Breakout Expansion aligned)

---

## CandidateSignal Persistence

| Option | Description | Selected |
|--------|-------------|----------|
| A — Write-then-update per step | Persist all candidates upfront (PENDING), update status at each filter step | |
| B — Approved only | Persist only ApprovedSignalORM rows + their linked candidates | |
| C — In-memory then batch write | Process all in memory, one transaction writes candidates (final status) + approved signals | ✓ |

**User pick:** C — in-memory processing, single batch DB transaction at end
