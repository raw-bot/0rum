# ADR-007 — Universal dynamic-exit routing

- Status: Accepted
- Date: 2026-07-22
- Supersedes: the AK-only scope of ADR-006; its causal and durability rules remain valid

## Context

Every paper sleeve must keep observing its position after entry, but the five
strategies do not share the same exit semantics. AK and HA have static
brackets and benefit from an additional MFE ratchet. UT Bot, Donchian and
Gold COT already emit causal, recalculated exits: the UT sell crossover, the
10-day Donchian channel break and COT gate OFF.

A 2024-01-01 through 2026-07-15 paired replay showed that attaching the same
1R/0.5R ratchet to every sleeve destroys rather than protects their edge. The
all-on portfolio ended at $17,919.86 versus $43,846.28 for native exits. The
problem is therefore routing, not the absence of a common abstraction.

## Decision

“Dynamic Égide” means continuous position management for every strategy, not
one universal formula:

| Sleeve | Égide route | Monitor | Extra MFE ratchet |
|---|---|---:|---:|
| AK MACD | static bracket + AK SSL + MFE | 15m | `ak_mfe_ssl_v1`, execute |
| UT Bot | UT sell crossover + hard stop | 15m | no; native exit won the replay |
| HA Trend | static bracket + generic MFE | 15m | `mfe_ratchet_v1`, execute |
| ETH Donchian | 10-day channel break | 1d | no; native exit won the replay |
| Gold COT | COT gate OFF | 1d/weekly gate | no; native exit won the replay |

The generic `mfe_ratchet_v1` capability is accepted for every engine, but it
is enabled only after a sleeve-specific paired replay. It has no code path to
AK SSL. The legacy `ak_mfe_ssl_v1` state remains loadable and unchanged in
meaning.

New policies are frozen per tranche at entry. Adopted positions must use an
explicit adoption epoch, their already-persisted positive `atr_risk`, and a
peak starting from the adoption mark; the containing candle is excluded.
No R value is reconstructed from future or rollout-time market data.

For each closed candle the engine keeps ADR-006 ordering:

1. apply the already-active hard/dynamic protective stop;
2. apply close-effective native/SSL invalidation if still open;
3. calculate a new stop, eligible only on the next contiguous candle;
4. persist the state or its single close through the existing account outbox.

## Selected paper experiment

AK uses the active 2R bracket with `activation_r=1.5`, `giveback_r=1.0` and
`floor_r=0.25`. In the actual AK+UT historical replay this raised final
equity from $35,200.33 to $42,128.67 (+$6,928.33) with no measurable increase
in maximum drawdown. These thresholds were selected and measured on the same
2024–2026 window: the result is in-sample exploration, not OOS validation.

The operator explicitly chose paper `execute` as the forward test rather than
an observe-only test-of-a-test. The forward paper epoch begins 2026-07-22; no
broker or live-order path exists here. HA retains `1.0/0.5/0.1`; its entry
sleeve remains disabled for the independent portfolio-cap reason already
documented.

## Consequences

- Every dashboard card identifies its Dynamic Égide route, including native
  strategy exits that do not carry MFE state.
- Adding MFE state to a strategy is an optional, versioned capability rather
  than a requirement for being dynamically managed.
- Current open tranches keep their frozen policy. Config changes apply only
  to new tranches.
- The replay harness can publish a causal historical COT gate and run paired
  per-sleeve plus interaction studies without touching runtime state.

## Rollback

Remove a sleeve's `dynamic_exit` block. Native signals and hard brackets then
follow their previous economic event trace; no data migration is required.
