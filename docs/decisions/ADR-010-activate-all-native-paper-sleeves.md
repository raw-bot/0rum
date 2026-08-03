# ADR-010: Activate all configured native paper sleeves

## Status

Accepted

## Date

2026-07-25

## Context

The native paper portfolio contained five configured strategies, but only BTC
UT Bot and BTC AK MACD accepted new entries. HA Trend was retained as a
counterfactual after replay cap interactions; ETH Donchian and Gold COT were
left in legacy entry-freeze states. This made the operational portfolio
narrower than its declared multi-strategy configuration without an active
operator decision to keep it that way.

The user explicitly requires all implemented strategies to operate in paper
mode unless a new explicit restriction is chosen.

## Decision

Enable new paper entries for all five configured sleeves: BTC AK MACD, BTC UT
Bot, BTC HA Trend, ETH Donchian and Gold COT. Preserve their existing signal
definitions, risk percentages, portfolio caps, sizing, stops and exit policies.

Gold COT remains conditional on its real weekly gate: an OFF gate is a valid
no-trade signal, not a disabled strategy. The existing merit order remains a
tie-breaker for simultaneous entries and is not an entry filter.

## Consequences

- The portfolio can take HA/ETH/Gold trades when their existing strategies
  generate an eligible signal.
- More simultaneous opportunities can consume the unchanged 5% total and 3%
  per-symbol stop-risk budgets; explicit risk-cap refusals remain visible.
- Earlier replay results remain historical evidence, not an implicit runtime
  disablement.
- The change is paper-only and does not authorize broker execution.
