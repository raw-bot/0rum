---
phase: 07-signal-mode-monitoring
plan: "03"
subsystem: execution
tags: [execution-router, pipeline-runner, signal-mode, trade-orm, risk-decision]
dependency_graph:
  requires:
    - "07-01"  # TradeORM schema (trailing_stop_price column)
    - "07-02"  # SignalSender implementation
  provides:
    - ExecutionRouter (src/execution/executor.py)
    - PipelineRunner 3-tuple threading (D-13)
    - TradeORM atomic creation with ApprovedSignalORM (D-14)
    - execution_status SENT update via re-query (D-15)
  affects:
    - src/pipeline/runner.py
    - src/execution/executor.py
tech_stack:
  added: []
  patterns:
    - re-query pattern to avoid DetachedInstanceError after session.begin() commits
    - TYPE_CHECKING guard for circular import prevention (ExecutionRouter -> PipelineRunner)
    - get_settings() called per-execute() (not cached at __init__) for test patchability
key_files:
  created:
    - src/execution/executor.py
    - tests/test_execution/test_executor.py
  modified:
    - src/pipeline/runner.py
    - tests/test_pipeline/test_runner_risk_step.py
decisions:
  - "ExecutionRouter calls get_settings() in execute() not __init__ — caching at init prevents test-time patching of execution_mode"
  - "PipelineRunner.__init__ accepts router=None — guards D-15 send block so pre-Plan-05 state accumulates PENDING signals without error"
  - "TradeORM created in same session.begin() as ApprovedSignalORM per D-14 — two flushes: one for candidate UUIDs, one after approved/trade inserts"
  - "D-15 uses re-query pattern: collect approved_ids before session expires, open second session per send to avoid DetachedInstanceError"
metrics:
  duration: "~8 minutes"
  completed: "2026-05-02"
  tasks_completed: 2
  files_changed: 4
---

# Phase 7 Plan 03: ExecutionRouter + PipelineRunner D-13/D-14/D-15 Summary

ExecutionRouter routes SIGNAL mode to SignalSender.send_signal() and raises NotImplementedError for AUTO mode (Phase 8 stub); PipelineRunner threads RiskDecision through 3-tuples, creates TradeORM atomically with ApprovedSignalORM, and updates execution_status to SENT using the re-query pattern.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | ExecutionRouter tests | d61986c | tests/test_execution/test_executor.py |
| 1 (GREEN) | ExecutionRouter implementation | 357fc2d | src/execution/executor.py |
| 2 | PipelineRunner D-13/D-14/D-15 + tests | 14a4ee5 | src/pipeline/runner.py, tests/test_pipeline/test_runner_risk_step.py |

## Verification Results

```
grep -c "list[tuple[CandidateSignal, float, RiskDecision]]" src/pipeline/runner.py → 2  ✓
grep -c "TradeORM(" src/pipeline/runner.py → 1  ✓
grep -c "size_lots=decision.sizing.size_lots" src/pipeline/runner.py → 2 (≥1 required)  ✓
grep -c "NotImplementedError" src/execution/executor.py → 4 (≥1 required)  ✓
grep -c "select(ApprovedSignalORM).where" src/pipeline/runner.py → 1  ✓
pytest tests/test_execution/ tests/test_pipeline/ → 40 passed  ✓
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] test_runner_risk_step.py: passing RiskDecision missing sizing**

- **Found during:** Task 2
- **Issue:** `test_risk_acceptance_path_creates_approved_orm` constructed `RiskDecision(passed=True, sizing=None)`. After D-14, `_persist()` accesses `decision.sizing.size_lots` for all approved signals, causing `AttributeError: 'NoneType' object has no attribute 'size_lots'`.
- **Fix:** Updated the test's `RiskDecision` to include a valid `PositionSizing(size_lots=Decimal("0.10"), ...)`. This is semantically correct — a passing RiskDecision must always have sizing populated (per `RiskDecision` docstring).
- **Files modified:** tests/test_pipeline/test_runner_risk_step.py
- **Commit:** 14a4ee5

**2. [Rule 1 - Bug] ExecutionRouter: get_settings() must not be cached at __init__**

- **Found during:** Task 1 (GREEN phase)
- **Issue:** Plan template called `self._settings = get_settings()` in `__init__`. Tests patch `src.execution.executor.get_settings` inside a `with patch(...)` block, but the router is instantiated before entering the patch context — so `self._settings` holds un-patched settings. `test_auto_mode_raises_not_implemented` failed because the mode was always `SIGNAL`.
- **Fix:** Moved `get_settings()` call into `execute()` body so the patch context is active when it runs.
- **Files modified:** src/execution/executor.py
- **Commit:** 357fc2d

## Known Stubs

None — no hardcoded empty values or placeholder text in produced code. The `router=None` default in `PipelineRunner.__init__` is an intentional intermediate state documented in the plan's notes section; Plan 05 wires the real router via main.py lifespan.

## Threat Flags

No new trust boundaries introduced beyond what the plan's threat model covers. T-07-03-03 (DoS via open transaction during HTTP) is mitigated: `execute()` is called after `session.begin()` commits in `_persist()`.

## Self-Check: PASSED

- src/execution/executor.py exists ✓
- tests/test_execution/test_executor.py exists ✓
- src/pipeline/runner.py modified ✓
- tests/test_pipeline/test_runner_risk_step.py modified ✓
- Commits d61986c, 357fc2d, 14a4ee5 in git log ✓
