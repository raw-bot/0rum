---
phase: 06-risk-management
plan: "01"
subsystem: testing
tags: [fakeredis, pytest, test-infrastructure, fixtures, circuit-breaker, redis]

requires:
  - phase: 05-backtesting-validation
    provides: "Completed test suite (208 tests green) as baseline"

provides:
  - "src/risk/__init__.py package stub — importable, documents planned public surface"
  - "tests/test_risk/__init__.py — pytest discovers the package"
  - "tests/test_risk/conftest.py — fake_redis, breaker, _mock_session, _reset_alert_hooks fixtures"
  - "fakeredis>=2.20 in pyproject.toml dependencies"

affects:
  - 06-02-hooks
  - 06-03-gates
  - 06-04-sizer
  - 06-05-breaker
  - 06-06-runner
  - 06-07-integration

tech-stack:
  added: [fakeredis>=2.20]
  patterns:
    - "Lazy fixture imports — BreakerManager and _alert_hooks imported inside fixture body, not at module level, so collection works before the modules exist"
    - "Function-scoped FakeAsyncRedis — never session/module-scoped to avoid event-loop binding (fakeredis-py #292)"
    - "autouse _reset_alert_hooks — clears module-global list before and after each test, ImportError caught for pre-module-existence safety"

key-files:
  created:
    - src/risk/__init__.py
    - tests/test_risk/__init__.py
    - tests/test_risk/conftest.py
  modified:
    - pyproject.toml

key-decisions:
  - "fakeredis goes in main dependencies (not optional-dependencies) — matches project precedent for pytest, respx, aiosqlite"
  - "All fixture imports of not-yet-existent modules (BreakerManager, _alert_hooks) are lazy — defers ImportError until the fixture is called, not at collection time"
  - "No make_signal() factory in conftest — project precedent is per-test-file duplication (5+ copies in test_pipeline/)"

patterns-established:
  - "Lazy-import pattern: fixtures that depend on future modules wrap the import inside the function body with try/except ImportError where needed"
  - "Function-scope-only for stateful fixtures backed by async libraries that bind to event loops"
  - "_reset_alert_hooks autouse pattern: clear global list before yield and after yield"

requirements-completed: []

duration: ~8min
completed: 2026-04-27
---

# Phase 6 Plan 01: Risk Test Infrastructure Summary

**fakeredis-backed pytest fixtures for tests/test_risk/ with lazy imports so all 6 downstream plans can add tests immediately without re-doing dependency wiring**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-04-27T00:00:00Z
- **Completed:** 2026-04-27T00:08:00Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Added `fakeredis>=2.20` to pyproject.toml and verified `from fakeredis import FakeAsyncRedis` imports cleanly
- Created `src/risk/__init__.py` stub with package docstring noting the planned public surface (RiskGateRunner, BreakerManager, register_alert_hook) without premature re-exports
- Created `tests/test_risk/conftest.py` with all four required fixtures using lazy imports, correct event-loop scoping, and autouse hook cleanup

## Task Commits

1. **Task 1: Add fakeredis to pyproject.toml** - `65ace95` (chore)
2. **Task 2: Create src/risk package stub and tests/test_risk marker** - `526734f` (feat)
3. **Task 3: Create tests/test_risk/conftest.py** - `8a82fb6` (feat)

## Files Created/Modified

- `pyproject.toml` - Added `"fakeredis>=2.20"` to main dependencies list (line 30)
- `src/risk/__init__.py` - Package marker with docstring documenting future re-exports; no active imports
- `tests/test_risk/__init__.py` - Zero-byte marker file; enables pytest package discovery
- `tests/test_risk/conftest.py` - Fixtures: `fake_redis` (function-scoped FakeAsyncRedis), `breaker` (lazy BreakerManager), `_mock_session` (helper), `_reset_alert_hooks` (autouse, lazy hooks import)

## Decisions Made

- Kept `fakeredis` in main `[project]` dependencies per existing project convention (`pytest`, `aiosqlite`, `respx` all follow the same pattern). No `[project.optional-dependencies.test]` group created.
- All imports of modules that don't yet exist (`src.risk.breaker`, `src.risk.hooks`) are deferred to fixture call-time, not collected at module import time — this is what allows plans 02-07 to run in any order without breaking collection.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

- `pip install -e .` was blocked by PEP 668 (externally-managed environment). Fell back to `pip install 'fakeredis>=2.20' --user` as the plan explicitly anticipated. No impact.
- `pytest tests/test_risk/ --collect-only -q` exits with code 5 ("no tests collected") rather than 0 — this is pytest's expected behavior for a directory with no test files. Collection itself succeeded with no errors. The acceptance criteria intent (no collection errors) is satisfied.
- `tests/test_backtesting/test_scheduler_wiring.py` fails with `ModuleNotFoundError: No module named 'apscheduler'` — this is a pre-existing environment issue present before this plan (committed in Phase 5, plan 05-03). The full suite excluding that file is 208 passed with 0 failures.

## Known Stubs

None — this plan creates infrastructure only, no data-path stubs.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Next Phase Readiness

- Plans 06-02 through 06-07 can all start immediately — `tests/test_risk/` is discoverable and `fake_redis`/`_mock_session` fixtures are available
- The `breaker` fixture will raise `ImportError` until plan 06-05 (BreakerManager) lands — this is expected and handled by the lazy import
- `_reset_alert_hooks` will silently no-op until plan 06-02 (hooks module) lands — the `try/except ImportError` path yields cleanly

---
*Phase: 06-risk-management*
*Completed: 2026-04-27*
