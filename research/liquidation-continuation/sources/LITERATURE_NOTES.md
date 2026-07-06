# Literature notes — liquidation cascades, perp microstructure, continuation hypothesis

## Serious sources (academic / exchange docs / SSRN-arXiv tier)

1. **Jingnan (Jane) ... "Liquidation, Leverage and Optimal Margin in Bitcoin Futures Markets"**, arXiv:2102.04591.
   Empirical study of BitMEX perpetual futures liquidations. Key facts used to motivate this
   research: daily forced liquidations are **3.51% of open interest for longs, 1.89% for
   shorts**; liquidated accounts had run **average leverage of 60x**; liquidated traders
   "trade aggressively" once forced out. This supports the mechanical premise of the
   continuation hypothesis (forced, price-insensitive flow in one direction over a short
   window) but the paper itself does not test post-liquidation price continuation —
   that gap is exactly what this project backtests empirically rather than assumes.
   https://arxiv.org/abs/2102.04591

2. **Zeeshan Ali, "Anatomy of the Oct 10–11, 2025 Crypto Liquidation Cascade: Macroeconomic
   Triggers, Market Microstructure, and Systemic Risk Lessons"**, SSRN 5611392.
   Event study of a real ~$19B OI liquidation cascade across 10 major cryptocurrencies on
   Binance hourly data. Documents the leverage→liquidity→volatility feedback loop
   mechanism that the continuation hypothesis depends on, but is a single-event case study,
   not a systematic trading-signal study.
   https://papers.ssrn.com/sol3/Delivery.cfm/5611392.pdf?abstractid=5611392

3. **"Risk-Based Auto-Deleveraging"**, arXiv:2603.15963, and **"Autodeleveraging:
   Impossibilities and Optimization"**, arXiv:2512.01112.
   Exchange-mechanism papers on how perpetual venues handle insurance-fund exhaustion and
   ADL during cascades. Useful background on *why* cascades can overshoot (ADL forces
   profitable counterparties to close too), supporting that continuation, if it exists, is
   a microstructure-driven, short-horizon effect rather than an information effect.

4. **Binance public data documentation** (github.com/binance/binance-public-data) and the
   data.binance.vision public dataset (official exchange-hosted historical data — klines,
   open interest / metrics, funding rate, and `liquidationSnapshot` for COIN-margined
   contracts). Used directly as the data source for this project; see DATA_NOTES.md for
   exact coverage and known limitations (the liquidationSnapshot feed is a *partial,
   non-exhaustive sample* of actual liquidation orders, not a full liquidation tape — this
   is a documented and widely discussed limitation, not specific to this project).

5. SSRN survey: Neubert, Rams, Gruhn, **"Cryptocurrency Perpetual Futures and Swaps: A
   Systematic Literature Review"**, SSRN 6639558 — confirms the academic literature on
   perpetuals is "strongest on market microstructure and risk management" but "thinner on
   trader behavior," i.e. there is no settled academic literature specifically validating
   liquidation-continuation as a tradeable edge. This project treats the hypothesis as
   open and falsifiable, consistent with that gap.

## Weak / idea-only sources (TradingView, Medium, exchange blogs — not treated as proof)

- Various CoinGlass / TradingView / Medium pieces on "liquidation heatmaps" describe
  liquidation clusters as **price magnets** that *attract* price (supports continuation
  toward a cluster) but other practitioner sources describe extreme long/short liquidation
  imbalance as a **crowded-positioning / mean-reversion** signal (price snaps back once
  the crowd is flushed) — i.e. retail commentary is split between continuation and
  reversal framings for the *same* underlying event. This directly motivated treating
  "continuation" as a hypothesis to falsify per strategy/regime rather than an assumed
  truth, and motivated explicitly comparing long vs short performance and chronological
  out-of-sample splits per strategy (see REPORT.md).
- Binance/OKX/Gate educational pages on OI, funding, and long/short ratio interpretation —
  used only to source plausible filter thresholds (e.g. "OI + price up = trend
  continuation, OI flat/down + price up = weak/short-covering"), not as evidence of edge.

## Implication for methodology

No serious source provides a validated, reproducible liquidation-continuation trading
rule with public performance data — only a plausible mechanical story (forced,
price-insensitive flow) and one real-world cascade case study. Combined with limited
real liquidation tape availability on Binance Data Vision (see DATA_NOTES.md), this
project (a) uses real liquidation data where available to calibrate a proxy, (b) builds
clearly-labeled proxy features for the main backtest universe, and (c) applies strict
rejection criteria so that any "successful" strategy reported here has cleared an
empirical bar, not just a narrative one.
