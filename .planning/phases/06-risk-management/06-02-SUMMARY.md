---
phase: 06-risk-management
plan: "02"
subsystem: risk
tags: [dtos, pydantic-v2, in-process-pubsub, frozen-models, hook-surface]

requires:
  - phase: 06-risk-management
    plan: "01"
    provides: "tests/test_risk/conftest.py with _reset_alert_hooks autouse fixture"

provides:
  - "src/risk/events.py — PositionSizing, RiskDecision, CircuitBreakerAlert Pydantic v2 frozen DTOs"
  - "src/risk/hooks.py — BreakerAlertHook type, _alert_hooks list, register_alert_hook, _publish_alert"
  - "tests/test_risk/test_hooks.py — 2 tests: happy-path and failure-isolation"

affects:
  - 06-04-sizer (consumes PositionSizing)
  - 06-05-breaker (emits CircuitBreakerAlert via _publish_alert)
  - 06-06-runner (returns RiskDecision)
  - 07-notif (registers Telegram hook via register_alert_hook)

tech-stack:
  added: []
  patterns:
    - "Frozen Pydantic v2 DTOs via ConfigDict(frozen=True) — intentional deviation from signal_data.py for cross-phase boundary safety (T-06-02-02)"
    - "Module-global hook list (_alert_hooks) with per-hook try/except isolation in _publish_alert (T-06-02-01, T-06-02-03)"
    - "AsyncMock-based pub-sub tests using assert_awaited_once_with — no codebase analog existed"

key-files:
  created:
    - src/risk/events.py
    - src/risk/hooks.py
    - tests/test_risk/test_hooks.py
  modified: []

key-decisions:
  - "risk_pct is float not Decimal: range 0.001-0.02 is fine for IEEE 754; risk_amount_usd and size_lots are Decimal to match TradeORM.pnl_pct money-math convention"
  - "No public reset function on _alert_hooks: conftest.py autouse fixture accesses the list directly; a public reset would invite production misuse"
  - "events.py is a leaf module with zero src.risk imports to prevent circular dependencies — hooks.py imports from events.py, not vice versa"

metrics:
  duration: ~2min
  completed: 2026-04-27
  tasks: 3
  files_created: 3
  files_modified: 0
---

# Phase 6 Plan 02: DTOs and Hook Surface Summary

**Pydantic v2 frozen DTOs (PositionSizing, RiskDecision, CircuitBreakerAlert) plus in-process pub-sub hook surface (_alert_hooks / register_alert_hook / _publish_alert) with two passing tests proving registration and failure isolation**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-04-27T00:00:00Z
- **Completed:** 2026-04-27T00:02:26Z
- **Tasks:** 3
- **Files created:** 3

## Accomplishments

- Created `src/risk/events.py` with three frozen Pydantic v2 DTOs matching the D-13 schema exactly: `PositionSizing` (ATR sizer output), `RiskDecision` (gate runner result), `CircuitBreakerAlert` (breaker trip event)
- Created `src/risk/hooks.py` with the in-process pub-sub surface: `BreakerAlertHook` type alias, `_alert_hooks` module-global list, `register_alert_hook` (sync append), `_publish_alert` (async fan-out with per-hook failure isolation and `noqa: BLE001`)
- Created `tests/test_risk/test_hooks.py` with two async tests proving the happy-path (hook awaited once with alert) and failure-isolation (failing hook does not raise, subsequent hook still receives alert)
- Full test suite: 210 passed (+2 from 208 baseline), 0 regressions

## Task Commits

1. **Task 1: src/risk/events.py** — `f4cba38` (feat)
2. **Task 2: src/risk/hooks.py** — `da0833c` (feat)
3. **Task 3: tests/test_risk/test_hooks.py** — `3d95b33` (test)

## Files Created

| File | Lines | Description |
|------|-------|-------------|
| `src/risk/events.py` | 94 | Three frozen Pydantic v2 DTOs |
| `src/risk/hooks.py` | 54 | In-process pub-sub hook surface |
| `tests/test_risk/test_hooks.py` | 55 | Two async tests for hooks |

## DTO Field Lists (verified against D-13)

**PositionSizing:**
- `risk_pct: float = Field(ge=0.0)` — final risk percentage after vol bump and concentration cap
- `risk_amount_usd: Decimal` — dollar risk for the trade
- `size_lots: Decimal` — position size in lots
- `vol_factor: float` — ATR volatility multiplier
- `concentration_reduced: bool` — True when concentration cap halved risk_pct

**RiskDecision:**
- `passed: bool` — whether the candidate cleared all risk gates
- `reason: Optional[str] = None` — rejecting gate name; None when passed=True
- `sizing: Optional[PositionSizing] = None` — None when passed=False
- `concentration_reduced: bool = False` — propagated from PositionSizing

**CircuitBreakerAlert (D-13):**
- `tripped_at: datetime` — UTC timestamp when breaker tripped
- `consecutive_stops: int` — number of stops that triggered the trip
- `cooldown_until: datetime` — UTC timestamp when breaker resets
- `last_stop_strategy: str` — strategy name of the final stop
- `last_stop_trade_id: Optional[UUID] = None` — UUID of final stop trade

## pytest Output

```
tests/test_risk/test_hooks.py ..                          [100%]
2 passed in 0.05s
```

Full suite (excluding pre-existing broken scheduler test):
```
210 passed in 9.10s
```

## Decisions Made

- `risk_pct` is `float` not `Decimal`: values in the 0.001–0.02 range do not require exact decimal arithmetic; `risk_amount_usd` and `size_lots` are `Decimal` to align with `TradeORM.pnl_pct` (Phase 7 will persist these fields).
- No public `reset_hooks()` function: the autouse `_reset_alert_hooks` fixture in `tests/test_risk/conftest.py` accesses `_alert_hooks` directly. A public reset would be a production misuse vector.
- `events.py` has zero `src.risk` imports (leaf module) to prevent the circular dependency that would arise if `hooks.py` importing `CircuitBreakerAlert` also triggered an `events.py` import of `hooks.py`.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all three DTOs are fully specified with real fields. No placeholder values, no hardcoded empty returns, no TODO comments in the data path.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or ORM schema changes introduced. The trust-boundary analysis in the plan's `<threat_model>` is satisfied:
- T-06-02-01 (repudiation): `_publish_alert` logs `risk.alert_hook.failed` on every hook failure
- T-06-02-02 (tampering): `CircuitBreakerAlert` frozen — mutation raises `ValidationError`
- T-06-02-03 (DoS): per-hook try/except ensures all hooks are visited regardless of upstream failures; enforced by `test_hook_failure_does_not_propagate`

## Self-Check: PASSED

- `src/risk/events.py` exists: FOUND
- `src/risk/hooks.py` exists: FOUND
- `tests/test_risk/test_hooks.py` exists: FOUND
- Commit `f4cba38` exists: FOUND
- Commit `da0833c` exists: FOUND
- Commit `3d95b33` exists: FOUND
- `grep -c "ConfigDict(frozen=True)" src/risk/events.py` = 3: PASSED
- `grep -c "Field(ge=0.0)" src/risk/events.py` = 1: PASSED
- `grep -c "from src.risk" src/risk/events.py` = 0: PASSED
- `grep -E -c "from telegram|import telegram|python_telegram_bot" src/risk/hooks.py` = 0: PASSED
- `grep -c "noqa: BLE001" src/risk/hooks.py` = 1: PASSED
- 2 tests passed in test_hooks.py: PASSED
- 210 total suite passed (no regressions): PASSED
