# ADR-008 — Adaptive profit runner

- Status: Accepted
- Date: 2026-07-23
- Extends: ADR-007

## Context

ADR-007 added continuous downside protection, but bracket strategies still
close at their original take-profit even when trend conditions remain strong.
That makes Dynamic Égide asymmetric: it can shorten a trade, but cannot let an
exceptional trade run beyond its initial objective.

UT Bot, Donchian and Gold COT are already uncapped and exit on a recalculated
native signal. AK MACD and HA Trend are the only sleeves whose static bracket
can cap the upside.

## Decision

For an explicitly enabled dynamic policy, the original take-profit becomes the
first active target rather than an immutable ceiling. Before the active target
is reached, Égide reviews it once price enters a configurable R buffer. A
causal trend-strength score uses only closed candles:

1. close above the slow EMA;
2. slow EMA rising;
3. fast EMA above the slow EMA;
4. positive three-bar return.

When at least three signals agree, the target is extended by one configured R
step. The new target is persisted and becomes eligible only on the next
candle. If strength is insufficient, the current target remains executable.
There is no arbitrary ceiling: repeated strength can extend 2R to 3R, then 4R
and beyond.

Protective ordering remains stop-first. The already-persisted hard/dynamic
stop is evaluated before the already-persisted active target. Target review
then updates only the next candle's target. A data or indicator failure leaves
the current target in force.

The feature is disabled by default for backward compatibility. It is enabled
for new AK and HA tranches. UT Bot, Donchian and COT retain their uncapped
native exits and receive no synthetic target.

## Consequences

- Dynamic Égide manages both downside protection and upside opportunity.
- The original `take_profit_price` remains the audited entry target; dynamic
  state records the active target, R level, extension count and strength score.
- Dashboard charts and tranche details show the active target when present.
- Existing tranches remain unchanged unless the operator explicitly adopts the
  new policy and records a fresh causal tracking epoch.

## Paper replay evidence

On the 2024-01-01 through 2026-07-15 in-sample replay, AK alone improved from
$15,110.33 with defensive Égide to $15,636.42 with the runner. The operational
AK+UT portfolio improved from $42,128.67 to $43,067.01. AK extended its target
23 times and reached an active target of 6.27 official ATR-R in its strongest
sequence. The forward paper epoch begins on 2026-07-23; these historical
figures are not an OOS or live-performance claim.

## Alternatives rejected

- Raising every fixed TP from 2R to 3R: still static and blind.
- Removing TP without a strength gate: exposes gains to unnecessary reversals.
- Extending after a target was touched using that candle's close: retroactively
  changes an order that should already have filled.
