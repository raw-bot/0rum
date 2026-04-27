---
phase: 06-risk-management
plan: "03"
subsystem: config
tags: [config, settings, decimal, pydantic-settings, risk]

requires:
  - phase: 06-risk-management
    plan: "01"
    provides: "src/risk package stub and tests/test_risk fixtures (wave 1 infrastructure)"

provides:
  - "src/config.py: theoretical_equity_usd: Decimal = Decimal('10000') field in Settings"
  - "from decimal import Decimal import added to src/config.py"
  - "tests/test_config/__init__.py: package marker"
  - "tests/test_config/test_settings.py: 3 tests covering default value, env override, Decimal arithmetic invariant"

affects:
  - 06-04-sizer
  - 06-06-runner
  - 06-07-integration

tech-stack:
  added: []
  patterns:
    - "Decimal field in pydantic-settings: typed as Decimal with Decimal(\"10000\") string-constructor default; env var parsed automatically via pydantic-settings v2"
    - "Pitfall 5 guard: multiply Decimal(str(float)) not Decimal(float) — avoids float imprecision in position sizing"
    - "monkeypatch.setenv pattern for env-override tests: set before constructing Settings() since get_settings() is not cached"

key-files:
  created:
    - tests/test_config/__init__.py
    - tests/test_config/test_settings.py
  modified:
    - src/config.py

key-decisions:
  - "Default equity is Decimal('10000') constant (D-09): dynamic equity tracking deferred to Phase 7+"
  - "String-arg constructor Decimal('10000') not Decimal(10000) — project convention for clarity and consistency"
  - "No custom pydantic validator needed: pydantic-settings 2.3+ parses env strings to Decimal natively"

patterns-established:
  - "Decimal field pattern: from decimal import Decimal; field: Decimal = Decimal('str_value')"
  - "Env-override test pattern: monkeypatch.setenv before Settings() construction (not after)"
  - "Arithmetic invariant test: lock Decimal(str(float)) multiplication pattern in test suite"

requirements-completed: [RISK-04]

duration: ~2min
completed: 2026-04-27
---

# Phase 6 Plan 03: Decimal Equity Baseline in Settings Summary

**`theoretical_equity_usd: Decimal = Decimal("10000")` added to Settings with env-override support and Pitfall 5 arithmetic invariant locked in test suite**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-04-27T12:48:07Z
- **Completed:** 2026-04-27T12:50:07Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Added `from decimal import Decimal` import and `theoretical_equity_usd: Decimal = Decimal("10000")` field to the existing risk block in `src/config.py` — zero other fields touched
- Created `tests/test_config/` package with 3 tests: default value, env-string-to-Decimal parsing, and the Decimal × Decimal(str(float)) arithmetic invariant the ATR sizer (Plan 04) depends on
- Full pytest suite passes 211 tests (208 pre-existing + 3 new); the pre-existing `apscheduler` collection error in `test_scheduler_wiring.py` is unchanged from before this plan

## Task Commits

1. **Task 1: Add theoretical_equity_usd: Decimal field to Settings** - `f669028` (feat)
2. **Task 2: Create tests/test_config/ with default + env-override tests** - `5a9466f` (test)

## Files Created/Modified

- `src/config.py` — Added `from decimal import Decimal` at line 3 (stdlib section) and `theoretical_equity_usd: Decimal = Decimal("10000")` after `hard_cap_risk: float = 0.02` in the risk block
- `tests/test_config/__init__.py` — Empty (0 bytes) package marker
- `tests/test_config/test_settings.py` — 3 sync tests: `test_theoretical_equity_default`, `test_theoretical_equity_env_override` (monkeypatch), `test_theoretical_equity_decimal_arithmetic_works`

## Decisions Made

- Used string-arg `Decimal("10000")` constructor per project convention (not `Decimal(10000)` with int, and definitely not float)
- No custom validator needed: pydantic-settings v2 parses env strings to `Decimal` natively
- `monkeypatch.setenv` used for env-override test (not `os.environ["..."]` with manual cleanup) — matches pytest best practice and handles teardown automatically

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- `tests/test_backtesting/test_scheduler_wiring.py` continues to fail with `ModuleNotFoundError: No module named 'apscheduler'` — this is the pre-existing issue noted in the 06-01 SUMMARY. Full suite excluding that file: 211 passed.

## User Setup Required

None - no external service configuration required.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced. The `theoretical_equity_usd` field reads from the operator environment only; T-06-03-01 (operator tampering) is accepted per the threat register since Phase 6 is signal-mode only.

## Known Stubs

None — `theoretical_equity_usd` is a concrete default value (`Decimal("10000")`), not a placeholder. Plan 04 (ATR sizer) will consume it directly.

## Next Phase Readiness

- Plan 04 (`src/risk/sizer.py`) can now import `settings.theoretical_equity_usd` and multiply it by `Decimal(str(risk_pct))` without casting issues
- The arithmetic invariant test in this plan ensures that pattern is locked before the sizer is written
- Plans 06-02, 06-05, 06-06 are unaffected by this change (no cross-dependency on the equity constant)

## Self-Check

Verified all files exist and commits present.

### Self-Check: PASSED

- `src/config.py`: found — contains `from decimal import Decimal` and `theoretical_equity_usd: Decimal = Decimal("10000")`
- `tests/test_config/__init__.py`: found — 0 bytes
- `tests/test_config/test_settings.py`: found — 3 tests, all pass
- Commit `f669028`: present in git log
- Commit `5a9466f`: present in git log
- 211 tests pass (208 pre-existing + 3 new)

---
*Phase: 06-risk-management*
*Completed: 2026-04-27*
