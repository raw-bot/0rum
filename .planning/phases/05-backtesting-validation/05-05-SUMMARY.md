---
phase: 05-backtesting-validation
plan: 05
subsystem: backtesting
tags: [integration-test, optimizer, walk-forward, tdd, wfe-gate, data-guard]
dependency_graph:
  requires: [05-04]
  provides: [optimizer-integration-tests]
  affects: [src/backtesting/optimizer.py]
tech_stack:
  added: []
  patterns: [structlog.testing.capture_logs, AsyncMock patch.object, pytest-asyncio]
key_files:
  created:
    - tests/test_backtesting/test_optimizer_integration.py
  modified: []
decisions:
  - "Used structlog.testing.capture_logs() context manager to assert optimizer.skipped.insufficient_data warning without modifying production logging config"
  - "Patched run_monte_carlo and monte_carlo_passes at module level in test 1 to ensure MC gate does not block activation — tests orchestration logic, not MC correctness"
  - "Kept _check_sufficient_data un-patched in test 3 so the real function validates the empty dict — tests the actual data guard code path"
metrics:
  duration: "~5 minutes"
  completed_date: "2026-04-23"
  tasks_completed: 1
  tasks_total: 2
---

# Phase 05 Plan 05: Full Statistical Validation Run — Partial Summary

**One-liner:** WalkForwardOptimizer integration tests verify activation path, WFE gate path, and data guard path using mocked DB and patched computation.

**Status: Task 1 complete — Task 2 (human-verify checkpoint) pending.**

---

## Task 1: Integration Tests — COMPLETE

### What was built

Created `tests/test_backtesting/test_optimizer_integration.py` with 3 integration tests verifying the orchestration logic of `WalkForwardOptimizer.run()`. All tests use mocked DB-touching methods — no live PostgreSQL required.

### Test Results

```
pytest tests/test_backtesting/test_optimizer_integration.py -x -q
3 passed in 4.29s

pytest tests/test_backtesting/ -x -q
52 passed in 4.96s

pytest -x -q
195 passed in 6.12s
```

Full suite is green with no regressions.

### Tests Implemented

| Test | Path Tested | Assertion |
|------|-------------|-----------|
| `test_optimizer_full_run_activates_params` | Sufficient data + passing combo → activation | `_activate_best_params` called exactly 4 times (once per strategy) |
| `test_optimizer_respects_wfe_gate` | `_evaluate_all_combos` returns `None` → no activation | `_activate_best_params.call_count == 0` |
| `test_optimizer_data_guard_on_empty_db` | Empty candle dict → early return + warning log | `call_count == 0` AND `optimizer.skipped.insufficient_data` in captured structlog output |

### Commit

- `32d4774`: `test(05-05): add WalkForwardOptimizer integration tests`

---

## Task 2: Human Verification Checkpoint — PENDING

Task 2 is a `checkpoint:human-verify` gate. The orchestrator is managing this as a user checkpoint after Task 1 completion.

**What to verify (from plan):**

1. Run `pytest -x -q` — all tests green
2. Verify scheduler registration (`run_optimizer` job, 24h trigger)
3. If Chemin B: trigger `WalkForwardOptimizer().run()` and query `optimizer_results` for `is_active=TRUE` rows with `wfe > 0.50`
4. If Chemin A: run optimizer and confirm `optimizer.skipped.insufficient_data` in log output
5. Verify all 6 Phase 5 ROADMAP.md success criteria are checkable

See `05-05-PLAN.md` Task 2 for full verification steps.

---

## Phase 5 Success Criteria Status

| # | Criterion | Status |
|---|-----------|--------|
| 1 | LHS 100 combos per strategy | Verified at code level (test_lhs_produces_100_combos in test_optimizer.py) |
| 2 | WFE > 50% gate | Verified by test_optimizer_respects_wfe_gate (no activation on None result) |
| 3 | Failed strategy retains previous params | Verified by test_optimizer_respects_wfe_gate (no deactivation call) |
| 4 | Multi-window 2/3 profitable gate | Verified by test_multiwindow_2_of_3_passes in test_walk_forward.py |
| 5 | Monte Carlo P95/P5 gates | Verified by test_monte_carlo.py |
| 6 | 24h scheduler job | Verified by test_scheduler_wiring.py |
| Real data | DB rows with wfe > 0.50 (Chemin B) | Pending human verification |

---

## Deviations from Plan

### Auto-fixed Issues

None — plan executed exactly as written.

The test for `test_optimizer_full_run_activates_params` additionally patches `run_monte_carlo` and `monte_carlo_passes` at the module level (as noted in the plan's design notes) to ensure the Monte Carlo gate does not interfere with testing the activation orchestration path. This is consistent with the plan's approach (a) described in the `<action>` section.

---

## Known Stubs

None — this plan creates tests only, no UI components or data-flow stubs.

## Threat Flags

None — this plan creates test-only files. No new network endpoints, auth paths, or schema changes introduced.

---

## Self-Check: PASSED

- [x] `tests/test_backtesting/test_optimizer_integration.py` exists
- [x] Commit `32d4774` exists in git log
- [x] 3 tests pass: `test_optimizer_full_run_activates_params`, `test_optimizer_respects_wfe_gate`, `test_optimizer_data_guard_on_empty_db`
- [x] Full suite: 195/195 green
