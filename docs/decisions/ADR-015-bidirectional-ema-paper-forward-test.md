# ADR-015: Bidirectional EMA 9/21 paper forward test

## Status

Accepted — paper-only activation 2026-08-03.

## Context

The research shadow EMA 9/21 sleeves closed five consecutive losses across BTC
and ETH.  The scalar `ADX > 20` check admitted a nearly flat BTC crossover and
two entries whose directional movement opposed the requested long.  The
strategy also emitted no native short entries, so bearish crossovers could only
close longs.

The unified paper ledger contained only one EMA loss; the other recent losses
belonged to UT Bot, Donchian and AK-MACD.  This decision therefore starts a new
EMA-specific forward-test epoch rather than attributing the mixed portfolio
drawdown to EMA.

## Decision

- Add separate `btc_ema_cross` and `eth_ema_cross` sleeves to the unified paper
  portfolio at 0.5% ATR-budget risk each.  No broker or live-order path is
  enabled.
- Keep the three-closed-H1-bar crossover confirmation and require all entry
  evidence to be causal and based on closed candles.
- Replace scalar ADX gating with directional trend quality:
  - long: `+DI > -DI`, positive slow-EMA slope and sufficient EMA separation;
  - short: `-DI > +DI`, negative slow-EMA slope and sufficient separation;
  - both directions require `ADX > 20` and non-falling ADX over the configured
    three-bar lookback.
- Normalize EMA separation and slope by ATR so the same policy can be observed
  on BTC and ETH.  The initial forward-test values are 0.20 ATR separation and
  0.05 ATR slow-EMA slope; these are experimental parameters, not OOS-validated
  champion values.
- A raw crossover does not emit the directionless `EXIT` signal. The opposite
  direction must earn its own three-bar confirmation and filters; the portfolio
  can then reverse an existing opposite position. Until then, the frozen stop
  remains the protective exit. This avoids a bullish raw cross accidentally
  closing a restored long (or the symmetric short case).
- Keep ADR-012's directional exit boundary:
  - longs use the frozen stop plus the existing causal `mfe_ratchet_v1` (`1R`
    activation, `1.5R` giveback, zero floor);
  - shorts do not receive long-only dynamic state and instead carry a complete
    frozen stop plus 2R take-profit bracket.
- Freeze policy state per new long tranche.  Existing positions are not
  retrofitted.

## Alternatives considered

- **Use ADX alone at a higher threshold.** Rejected because ADX strength is not
  directional and may remain high during a counter-trend bounce.
- **Enable shorts without a target.** Rejected because ADR-012 requires every
  persisted native-paper short to have a complete reloadable bracket.
- **Mirror the dynamic MFE manager for shorts now.** Rejected because its price
  geometry and persisted invariants remain intentionally long-only.
- **Increase crossover confirmation further.** Rejected as the primary fix;
  more delay does not identify flat or directionally opposed regimes.

## Consequences

- BTC and ETH EMA performance is attributable in the official paper ledger by
  strategy ID and direction.
- The new filters may substantially reduce trade count.  Win rate alone is not
  an acceptance metric; expectancy, profit factor, exposure, drawdown and
  per-direction results remain required.
- BTC sleeves share the existing symbol risk cap, so the merit auction can
  reject an EMA request when higher-priority BTC risk already consumes the cap.
- Synthetic spot shorts remain a paper accounting experiment.  No claim about
  margin, funding, borrowing or broker executability follows from their result.

## Verification and rollback

- Unit tests cover long acceptance, flat rejection, opposed DI rejection,
  falling-ADX rejection, symmetric short brackets, short disablement, raw-cross
  waiting behavior and one real-indicator trend fixture.
- Runtime configuration tests require both sleeves in the committed and
  operator override portfolios.
- Rollback is to set both EMA sleeves `entry_enabled: false` (or remove them)
  for future entries.  Open positions retain their frozen stop/target/dynamic
  state and remain eligible for protective exits.
