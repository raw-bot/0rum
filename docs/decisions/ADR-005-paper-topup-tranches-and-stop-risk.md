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

Size and cap new positions using the actual entry-to-stop `risk_distance` when
a valid explicit stop exists. Use the legacy ATR distance only when the signal
supplies no stop. Reject malformed explicit stops. Legacy positions without a
stored `risk_distance` fall back to their persisted `atr_risk`.

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

Rejected because presentation would improve while the risk cap and quantity
would remain economically wrong.

## Consequences

- Paper exposure can increase through repeated qualified signals until the
  configured symbol or portfolio budget is exhausted.
- Corrected sizing changes the numerical replay results; legacy topup returns
  cannot be presented as the corrected policy's expected performance.
- State and fills gain tranche identity and actual-risk audit fields.
- Existing strategy attribution remains stable.
- Old persisted positions remain readable without rewriting live state.
- Broker-real short, margin, and execution concerns remain out of scope.

## Detailed Design

See
`docs/superpowers/specs/2026-07-17-paper-topup-stop-risk-design.md`.
