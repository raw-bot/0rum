# ADR-005: Risk Accounting And Backtest Parity

## Status
Accepted

## Date
2026-05-25

## Context
The structural audit found that live signal-mode tracking, risk gates, and
optimizer simulations used inconsistent financial units. Some paths treated
`pnl_pct` as a price return, others as account performance, while sizing used a
static equity value and backtests omitted the same execution costs seen by
signal-mode monitoring.

The project also has a strict provider boundary: Binance/CCXT PAXG/USDT is
runtime plumbing only, while Dukascopy public `.bi5` is the validated research
source for XAUUSD strategy validation.

## Decision
Use one account-return accounting model across signal-mode monitoring, risk
gates, and optimizer evaluation:

- `TradeORM.pnl` is realized USD P&L.
- `TradeORM.pnl_pct` is account return, not XAUUSD price return.
- `TradeORM.equity_at_open` is captured when a theoretical trade is created and
  used to compute realized account return on close.
- Risk gates use dynamic paper equity instead of static configured equity.
- Aggregate gates reject excessive account leverage, stop-risk exposure, and
  max equity drawdown before approving a new theoretical trade.
- Signal-mode and optimizer simulations both apply XAUUSD contract sizing,
  spread/slippage costs, TP1/trailing behavior, and trade expiry semantics.
- Optimizer activation requires research-grade candle provenance and must not
  activate parameters from runtime-proxy Binance/PAXG data.

## Alternatives Considered

### Keep `pnl_pct` as price return
- Pros: Matches the old implementation and is easy to calculate from prices.
- Cons: Daily loss limits and optimizer scores become unrelated to account risk.
- Rejected: The bot manages account risk, so account return is the correct unit.

### Use configured starting equity for all sizing
- Pros: Simple and deterministic.
- Cons: Risk per trade rises after losses and falls after gains, hiding drawdown.
- Rejected: Signal-mode must behave like a real paper account.

### Allow optimizer validation on runtime proxy candles
- Pros: Easier to run with existing plumbing.
- Cons: PAXG/USDT proxy data is not validated XAUUSD research data.
- Rejected: It violates the provider truth override and contaminates strategy
  validation.

## Consequences
- Dashboard, `/health`, risk decisions, and optimizer results now speak the same
  accounting language.
- Backtest scores are lower but more realistic because costs, expiry, and
  account sizing are included.
- Legacy trades without `equity_at_open`, `notional_usd`, or `risk_amount_usd`
  need fallback handling and should not be treated as fully comparable to new
  paper-account rows.
- Legacy candle rows without source provenance must be reimported or repaired
  before optimizer activation can treat them as Dukascopy research data.
