# Kraken EUR universe — provisional Donchian screen

## Run contract

- Retrieved: 2026-07-10 22:36:36 UTC (2026-07-11 Europe/Paris)
- Venue/feed: Kraken public Spot OHLC through CCXT 4.5.56
- Markets: BTC/EUR, ETH/EUR, SOL/EUR, XRP/EUR, AAVE/EUR
- Timeframe: daily
- Finalized bars per market: 720
- Window: 2024-07-20 through 2026-07-09
- Strategy: long-only Donchian 20-bar entry / 10-bar exit
- Execution: signal on confirmed close, fill at following open
- Costs: 0.10% round trip plus 0.03% slippage per fill
- Risk normalization: 2 × Wilder ATR14 at the entry-signal bar
- Chronological split: first 70% train, last 30% holdout
- Boundary-spanning trades: excluded from both train and holdout

Kraken documents that its OHLC endpoint returns at most 720 recent entries and
always includes the current uncommitted candle. The provider removed that final
incomplete row before the screen.

Sources:

- https://docs.kraken.com/api-reference/market-data/get-ohlc-data
- https://github.com/ccxt/ccxt/wiki/manual#ohlcv-candlestick-charts

## Results

| Pair | Full trades | Full PF | Full expectancy | Holdout trades | Holdout PF | Holdout expectancy | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| BTC/EUR | 13 | 1.70 | +0.417R | 3 | 1.20 | +0.067R | Provisional only |
| ETH/EUR | 15 | 0.97 | −0.017R | 5 | 0.00 | −0.653R | Reject |
| SOL/EUR | 12 | 5.85 | +0.424R | 4 | 0.06 | −0.226R | Reject |
| XRP/EUR | 12 | 9.01 | +3.802R | 4 | 0.00 | −0.480R | Reject; outlier-dominated train |
| AAVE/EUR | 15 | 0.68 | −0.230R | 5 | 0.00 | −1.088R | Reject |

## Decision

Do not add any EUR strategy to the funded paper portfolio from this screen.
BTC/EUR is the only holdout-positive pair, but three trades cannot establish an
edge. The other four pairs reverse from attractive or neutral training results
to negative holdout results, so their full-sample numbers are not actionable.

Keep BTC/USDT as the existing data/legacy lane and preserve the open BTC/USDT
and ETH/USDT positions. The legacy configurations are entry-disabled so, after
an eventual exit, they cannot silently reopen while the EUR migration remains
unvalidated.

The next evidence step requires a longer historical source or a forward paper
sample. It must not tune Donchian parameters against these 720 bars and then
reuse the same holdout.
