# ADR-016: Activate Paper Capital-Preservation Overrides

## Status

Accepted; the 5% drawdown entry halt is superseded by ADR-017.

## Date

2026-09-02

## Context

The unified paper portfolio grew from roughly USD 9,300 to roughly USD 14,000,
but most of that gain came from one BTC UT Bot long. The same review found that
main-account shorts were unprofitable, multiple BTC sleeves could express the
same thesis simultaneously, and the paper broker sized on a legacy 2x-ATR
distance even when the frozen stop was materially farther away.

ADR-005 explicitly kept ATR sizing after a return-maximizing replay. That
choice produced higher simulated return but also hid the loss implied by the
real stop. The operator now prefers survival and auditable risk over preserving
that more aggressive return profile.

The legacy drawdown guardrail still exists in the mono-asset worker, but the
unified portfolio did not call it. Signal fills also used the signal candle's
close, which is not the next executable bar in a causal forward test.

## Decision

The scheduled unified paper portfolio activates these explicit overrides:

- Entry sides are long-only. Existing short positions are not rewritten or
  force-closed; protective exits and native strategy exits remain active.
- Position quantity and portfolio risk budgets use the frozen entry-to-stop
  distance. If a strategy has no explicit stop, the existing ATR distance is
  the fail-closed fallback.
- New entries halt when account equity is at least 5% below the highest
  recorded paper equity. Exits remain available. The check reads the unified
  equity journal, not the retired mono-asset goal path.
- BTC is limited to one open position across all main-account strategies and
  aggregate BTC notional is capped at 100% of current account equity.
- Directional signals are evaluated on the prior fully closed candle and filled
  at the following candle's open. Protective stop processing remains separate.
- A BTC H4 close-versus-EMA200 regime observation is logged in shadow. The
  configuration rejects any attempt to set the shadow filter to enforced mode.
- UT Bot remains enabled and keeps first priority in the merit order.

Short research moves to a separate, manually invoked paper account with its own
configuration, lock, positions, fills, equity and shadow journals. It is
short-only, risks 0.25% per signal, caps total/symbol stop risk at 0.5%, caps BTC
notional at 25% of equity and is not attached to launchd.

Historical configurations retain the old defaults unless they explicitly
select these overrides. This preserves old replay reproducibility while making
the live paper configuration unambiguous.

## Alternatives Considered

### Predict only the next exceptional trend

Rejected. No causal detector can guarantee which qualifying signal will become
the next outlier. Fewer, bounded opportunities are preferable to a
return-maximizing selector fitted to one winner.

### Force-close the currently open paper short

Rejected. Rewriting the ledger would destroy forward-test continuity. The
position remains protected and no new main-account short can open.

### Keep ATR sizing because it produced the larger replay return

Rejected for the active paper account. The comparison measured return, not the
honesty of loss-at-stop. Actual-stop sizing is deliberately expected to reduce
both return and hidden tail risk.

### Activate the H4 filter immediately

Rejected. It must first accumulate causal observations across multiple market
regimes and enough independent entries to measure missed winners as well as
avoided losers.

## Consequences

- At the time of activation, recorded paper equity is about 12.8% below its
  historical peak. The 5% kill-switch therefore blocks new main-account entries
  immediately while leaving existing exits operational.
- Main-account trade frequency and simulated return can fall substantially.
- A tight stop can still imply a large quantity, but the BTC aggregate notional
  cap shrinks the fill before it reaches the ledger.
- Existing positions remain readable: positions without
  `risk_sizing_basis` default to the historical ATR basis.
- The shadow regime decision is append-only and cannot affect a fill.
- This ADR authorizes paper behavior only. It does not authorize broker orders
  or a live execution path.

## Observation Protocol

Do not promote the H4 filter or relax a safety cap from a handful of trades.
Collect at least several dozen independent entry candidates spanning bullish,
bearish, sideways and high-volatility periods. Compare:

- baseline fill outcome versus the H4 shadow verdict;
- realized and maximum-adverse excursion in actual-stop R;
- missed large winners and avoided full-stop losses;
- BTC notional and stop risk before and after caps;
- drawdown duration while the entry kill-switch is active.

Any future activation or threshold change requires a new ADR and a causal
walk-forward replay. Do not edit this ADR to erase the original decision.

## Rollback

Rollback is configuration-first and paper-only:

1. Stop invoking the manual short experiment.
2. Restore `execution_mode: signal_close` only after a parity replay.
3. Restore `risk_sizing_basis: atr` only with explicit operator acceptance of
   hidden loss-at-stop risk.
4. Remove or raise a cap only in a new ADR with measured evidence.

Existing paper ledgers must never be rewritten as part of rollback.
