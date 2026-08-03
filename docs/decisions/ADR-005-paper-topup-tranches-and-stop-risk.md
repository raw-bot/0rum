# ADR-005: Promote Paper Topups With Tranche Identity And Actual Stop Risk

## Status

Accepted

## Date

2026-07-17

## Context

The replay thesis arbiter showed that bounded topups improved the tested
AK-MACD/UT-Bot portfolio relative to implicit hold-only re-entry, including
under execution stress. The operator selected direct promotion to the paper
portfolio because this runtime does not place broker orders.

The current paper model also sizes positions and enforces caps with a legacy
`2 * ATR(14)` distance even when a strategy supplies a materially different
protective stop. The 2026-07-16 UT Bot trade exposed the mismatch: its stop was
3.33 times the sizing distance, so nominal 0.75% risk did not represent the
loss at the frozen stop.

The harness prototype represents tranches by mutating `strategy_id`. That is
not suitable for persisted paper state because dashboard and reporting
consumers use `strategy_id` for attribution.

## Decision

Promote `topup` directly into the paper engine with static merit UT Bot before
AK MACD and no minimum grant fraction. Store every topup as an independent
position tranche with a unique `position_id` and a stable base `strategy_id`.

Position sizing, portfolio/thesis risk caps, and R-multiple reporting keep
using the legacy `2 * ATR(14)` distance (`atr_risk`), unchanged from before
this ADR. The actual entry-to-stop distance (`risk_distance`) is computed and
persisted alongside every position, and a malformed explicit stop is still
rejected as `invalid_stop` — but `risk_distance` itself only feeds the
dashboard's audit display, not sizing, caps, or R. See "Alternatives
Considered" below: the replay comparison showed +390.91% (ATR-based, kept)
versus +118.23% (actual-stop-based), so the operator chose to keep the
validated ATR sizing. Legacy positions without a stored `risk_distance` fall
back to their persisted `atr_risk`.

Protective levels close one tranche; a strategy `EXIT` closes every tranche of
that strategy. No process is restarted as part of implementation.

## Alternatives Considered

### Keep topups in shadow

Rejected by the operator. The target runtime is paper-only, and the replay
policy has already been selected for direct paper promotion.

### Encode tranche suffixes in `strategy_id`

Rejected because it corrupts stable attribution and requires every dashboard
and reporting consumer to reinterpret strategy identifiers.

### Merge topups into one average-price position

Rejected because it cannot preserve the independently frozen bracket attached
to each signal and would not match the validated replay semantics.

### Keep ATR sizing while displaying actual stop risk

Originally rejected, then explicitly selected by the operator after comparing
the corrected replay (+118.23%) with the historical ATR replay (+390.91%).

## Consequences

- Paper exposure can increase through repeated qualified signals until the
  configured symbol or portfolio budget is exhausted.
- Sizing and cap math are unchanged from the pre-ADR-005 behavior (still
  ATR-based); no replay results were invalidated by this change.
- State and fills gain tranche identity and actual-risk audit fields
  (`risk_distance`, `stop_risk_usd`) for dashboard display only — they do not
  feed any sizing, cap, or R calculation.
- Existing strategy attribution remains stable.
- Old persisted positions remain readable without rewriting live state.
- Broker-real short, margin, and execution concerns remain out of scope.

## Detailed Design

See
`docs/superpowers/specs/2026-07-17-paper-topup-stop-risk-design.md`.
