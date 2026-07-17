# ETH/EUR volatility edge — long-history walk-forward

## Outcome

The original hypothesis is supported: ETH/EUR contains a substantial long-run
trend edge that the first 720-day Kraken screen understated. The recent regime
is weak, however, and the new filtered challenger is not stable enough for
automatic activation.

No strategy or pair was added to the paper portfolio by this research run.

## Data contract

- Retrieved: 2026-07-10 23:31:03 UTC (2026-07-11 Europe/Paris)
- Coinbase Exchange BTC/EUR: 4,093 finalized daily bars, 3 missing buckets
- Coinbase Exchange ETH/EUR: 3,333 finalized daily bars, 2 missing buckets
- Common BTC/ETH history: 2017-05-23 through 2026-07-09
- Kraken portability sample: 720 finalized daily bars per pair
- Execution: confirmed close, fill at following open
- Costs: 0.10% round trip plus 0.03% slippage per fill
- Risk normalization: 2 × Wilder ATR14 at the entry signal
- Validation: 730-bar warm-up followed by seven non-overlapping 365-bar windows
- No parameter search and no forced close at the end of a sample

Coinbase documents a 300-candle maximum per request and warns that empty trade
intervals may be absent. Retrieval was paginated and gaps were preserved rather
than filled. Kraken's official downloadable all-market archive was not used in
this run because it is approximately 7.9 GB; Kraken's recent API sample was used
as an independent venue-portability check.

Sources:

- https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
- https://docs.kraken.com/api-reference/market-data/get-ohlc-data
- https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data

## Venue portability

| Pair | Common bars | Daily-return correlation | Median absolute return difference | Gate |
|---|---:|---:|---:|---|
| BTC/EUR | 720 | 0.99975 | 0.0279% | Pass |
| ETH/EUR | 720 | 0.99986 | 0.0353% | Pass |

Both correlations exceed the frozen 0.995 threshold and both median differences
remain below 0.20%. The long Coinbase EUR history is therefore a credible proxy
for testing a strategy intended for Kraken EUR paper execution.

## Aggregate out-of-sample results

| Model | Trades | PF | Expectancy | Net | Max DD | Positive windows | Exposure |
|---|---:|---:|---:|---:|---:|---:|---:|
| Donchian 20/10 benchmark | 52 | 2.69 | +0.800R | +41.58R | 6.42R | 6/7 (85.7%) | 38.5% |
| ETH filtered challenger | 22 | 2.67 | +0.866R | +19.04R | 7.71R | 4/7 (57.1%) | 17.7% |

The challenger improves expectancy per trade and cuts exposure by more than
half, but it fails the pre-registered stability gate of at least 60% positive
windows. It therefore remains research-only.

## Regime detail

| Test window | Donchian expectancy | Challenger expectancy |
|---|---:|---:|
| 2019-05-25 — 2020-05-23 | +0.828R | +1.864R |
| 2020-05-24 — 2021-05-23 | +2.787R | +3.298R |
| 2021-05-24 — 2022-05-23 | +0.987R | +1.054R |
| 2022-05-24 — 2023-05-23 | +0.274R | −1.229R |
| 2023-05-24 — 2024-05-22 | +0.487R | −0.127R |
| 2024-05-23 — 2025-05-22 | +0.689R | −0.461R |
| 2025-05-23 — 2026-05-22 | −0.327R | +0.543R |

This reconciles the apparently contradictory screens. ETH Donchian has a strong
long-run edge, but the latest complete yearly window is its only negative one.
The earlier Kraken-only holdout was concentrated in this unfavorable recent
regime and contained only five closed trades.

## Decision

1. Preserve the current ETH/USDT Donchian paper position and allow its normal
   exit; do not duplicate ETH exposure with a new ETH/EUR position.
2. Keep legacy entries disabled after restart so BTC/USDT and ETH/USDT cannot
   silently reopen during migration.
3. Retain ETH/EUR Donchian as the leading migration candidate. A separate
   activation review must first add common-currency accounting and an ETH risk
   bucket, then start as forward paper only.
4. Keep the filtered challenger in shadow research because it missed its frozen
   stability threshold. Do not lower the 60% gate after seeing 57.1%.

Machine-readable evidence is stored in
`state/data_cache/eth_edge_report.json` with the four source candle files beside
it.
