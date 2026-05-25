# ADR-004: Signal-Mode Parity For Backtest Validation

## Status
Accepted

## Date
2026-05-22

## Context

The live signal-mode tracker and the walk-forward optimizer were measuring different trade lifecycles.

The live theoretical tracker uses:

- M15 candles for SL/TP touches
- TP1 as a partial close
- ATR(H1) trailing after TP1
- blended realized `pnl_pct`

The optimizer previously used a simpler H1-only outcome simulation:

- full-position SL/TP1/TP2
- no TP1 partial
- no ATR trailing
- price-unit PnL

The optimizer also slid one H1 candle at a time, so the same strategy setup could be counted repeatedly across adjacent windows. That made backtest/live comparison methodologically noisy, especially after the runtime pipeline began deduplicating repeated signal setups.

## Decision

Walk-forward validation must simulate the signal-mode lifecycle used by the live theoretical tracker.

The optimizer now:

1. uses M15 candles for post-signal touch detection
2. starts ATR(H1) trailing after TP1
3. returns blended percentage PnL, matching `TradeORM.pnl_pct`
4. deduplicates repeated simulated setups within the same 60-minute window rule used by the signal pipeline

The legacy `simulate_trade_outcome()` helper remains for focused unit tests and older simple outcome semantics, but optimizer validation uses the signal-mode simulator.

## Consequences

Optimizer metrics are now closer to paper-live signal-mode results and less likely to overstate repeated setups from overlapping walk-forward windows.

Historical optimizer rows produced before this decision are not directly comparable with new optimizer rows. Any future backtest/live audit should compare results generated after this change.

The implementation still does not model broker execution details, slippage, spread, partial fill risk, or live XAUUSD broker constraints. Those remain Phase 8 concerns.
