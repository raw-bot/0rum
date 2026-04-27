---
phase: 06-risk-management
plan: "04"
subsystem: risk
tags: [sizer, atr, position-sizing, pure-function, tdd, risk-04]
dependency_graph:
  requires: [06-02, 06-03]
  provides: [calculate_position_size, PositionSizing]
  affects: [06-07-runner]
tech_stack:
  added: []
  patterns: [pure-function, decimal-arithmetic, tdd-red-green]
key_files:
  created:
    - src/risk/sizer.py
    - tests/test_risk/test_sizer.py
  modified: []
decisions:
  - "atr_value accepted in signature but unused in v1 math — reserved for future ATR-based stop-distance sanity checks"
  - "Decimal(str(risk_pct)) used throughout to guard against float->Decimal precision corruption (Pitfall 5)"
  - "No structlog in sizer — orchestrator (Plan 07) emits risk.sizing.calculated after calling"
metrics:
  duration_minutes: 275
  completed_date: "2026-04-27"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
---

# Phase 6 Plan 04: ATR Position Sizer (RISK-04) Summary

**One-liner:** Pure synchronous ATR-based position sizer with vol->cap->concentration order enforcement, Decimal precision guards, and 8 TDD tests locking all load-bearing math invariants.

## Function Signature Confirmation

```python
def calculate_position_size(
    *,
    equity: Decimal,
    risk_per_trade: float,
    entry_price: Decimal,
    sl_price: Decimal,
    atr_value: Decimal,        # reserved; not used in v1 math
    atr_pctile: float,          # 0.0-1.0 scale (RegimeDetector output)
    hard_cap: float,
    atr_high_vol_pctile: int,   # whole number e.g. 90 -> normalized to 0.90
    atr_low_vol_pctile: int,    # whole number e.g. 10 -> normalized to 0.10
    same_direction_open_count: int,
) -> PositionSizing:
```

Located in `src/risk/sizer.py`. Returns frozen `PositionSizing` DTO from `src/risk/events.py`.

## Cap-After-Vol Verification

The plan's key correctness canary — input `(risk_per_trade=0.018, atr_pctile=0.05, same_direction_open_count=0)`:

| Step | Operation | Result |
|------|-----------|--------|
| 1 | vol_factor (low-vol branch: 0.05 <= 0.10) | 1.3 |
| 2 | risk_pct = 0.018 * 1.3 | 0.0234 |
| 3 | risk_pct = min(0.0234, 0.02) | **0.02** |
| 4 | concentration_reduced (0 < 4) | False |

`test_hard_cap_clamps_after_low_vol_bump` asserts `risk_pct == pytest.approx(0.02)`. A cap-then-vol order bug would yield `0.0234` and fail loud.

## Test Names and Pass Count

All 8 tests pass (`pytest tests/test_risk/test_sizer.py -x -q`: `8 passed in 0.08s`):

| # | Test Name | What It Locks |
|---|-----------|---------------|
| 1 | `test_baseline_normal_vol_no_concentration` | Normal regime baseline math |
| 2 | `test_high_vol_reduces_30_percent` | vol_factor=0.7 at atr_pctile=0.95 |
| 3 | `test_low_vol_increases_30_percent` | D-16 symmetric +30% branch |
| 4 | `test_hard_cap_clamps_after_low_vol_bump` | D-07 vol-then-cap order |
| 5 | `test_concentration_halves_after_cap` | D-04 + D-07 cap-then-halve order |
| 6 | `test_atr_pctile_scale_invariant` | Pitfall 1: settings 90 != atr_pctile 0.90 |
| 7 | `test_size_lots_zero_when_no_sl_distance` | T-06-04-04 zero-division guard |
| 8 | `test_size_lots_quantized_to_two_decimals` | quantize(Decimal("0.01")) enforced |

Full suite (excluding pre-existing `apscheduler` import failure in test_scheduler_wiring.py): **221 passed**.

## TDD Gate Compliance

- RED commit: `c323340` — `test(06-04): RED — failing sizer tests for RISK-04`
- GREEN commit: `7bf1f58` — `feat(06-04): GREEN — implement ATR sizer (RISK-04)`

Gate sequence: RED (test commit) -> GREEN (feat commit). Compliant.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. `calculate_position_size` is fully implemented with live math. No hardcoded returns, no placeholder values.

## Threat Flags

None. No new network endpoints, auth paths, file access patterns, or schema changes introduced. All six threat register items (T-06-04-01 through T-06-04-06) are addressed by the implementation and test coverage as planned.

## Self-Check: PASSED

- `src/risk/sizer.py` exists and contains `def calculate_position_size`
- `tests/test_risk/test_sizer.py` exists with 8 test functions
- RED commit `c323340` exists in git log
- GREEN commit `7bf1f58` exists in git log
- `pytest tests/test_risk/test_sizer.py`: 8 passed
