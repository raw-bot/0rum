# ADR-002: Let a calibrated forecast gate influence paper entries

## Status

Accepted for the unified paper portfolio

## Date

2026-07-11

## Context

The dashboard scenario fan was synthetic and explicitly display-only. Keeping a
decorative forecast suggests information the trading system does not possess.
The user selected a medium-control policy: filter and adaptive sizing, still in
paper, without a second portfolio.

## Decision

Add a deterministic, walk-forward evaluated 1-hour return forecaster for +6 h,
+12 h and +24 h. It may act only on new long entries and only after fixed
calibration thresholds are met. Every action records the unmodified strategy
decision and the modified outcome. Missing or insufficient evidence is fail
closed to the baseline behavior: multiplier `1.0`, no veto.

The dashboard must render the same persisted quantiles that the engine used.
Synthetic paths may remain only as an explicitly labelled visual fallback when
no calibrated state exists.

## Consequences

- The forecast cannot alter current positions or exit logic.
- A model that fails validation remains visible but has no control.
- Position sizing stays inside the existing broker and risk budget.
- Forecast quality and decisions become auditable in append-only state.
- Operational activation still requires a later explicit process restart.

